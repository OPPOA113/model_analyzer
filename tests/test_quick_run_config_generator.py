# Copyright (c) 2022, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest
from unittest.mock import MagicMock, patch

from .common import test_result_collector as trc
from model_analyzer.config.generate.coordinate import Coordinate
from model_analyzer.config.generate.search_config import SearchConfig
from model_analyzer.config.generate.search_dimension import SearchDimension
from model_analyzer.config.generate.search_dimensions import SearchDimensions
from model_analyzer.config.generate.model_variant_name_manager import ModelVariantNameManager
from model_analyzer.config.generate.run_config_generator_factory import RunConfigGeneratorFactory
from model_analyzer.config.generate.quick_run_config_generator import QuickRunConfigGenerator
from model_analyzer.config.input.objects.config_model_profile_spec import ConfigModelProfileSpec
from model_analyzer.config.generate.model_profile_spec import ModelProfileSpec

from tests.common.test_utils import evaluate_mock_config


def mock_ensemble_configs(*args, **kwargs):
    fake_config = {
        "name": "my-model",
        "platform": "ensemble",
        "ensemble_scheduling": {
            "step": [{
                "model_name": "fake_model_A"
            }, {
                "model_name": "fake_model_B"
            }]
        },
        "input": [{
            "name": "INPUT__0",
            "dataType": "TYPE_FP32",
            "dims": [16]
        }],
        "max_batch_size": 4
    }
    fake_base_subconfig0 = {
        "name": "fake_model_A",
        "input": [{
            "name": "INPUT__0",
            "dataType": "TYPE_FP32",
            "dims": [16]
        }],
        "max_batch_size": 4,
        "sequence_batching": {}
    }
    fake_base_subconfig1 = {
        "name": "fake_model_B",
        "input": [{
            "name": "INPUT__2",
            "dataType": "TYPE_FP16",
            "dims": [32]
        }],
        "max_batch_size": 8
    }

    if args:
        model_name = args[4]
    else:
        model_name = kwargs['model_name']

    if model_name == 'my-model':
        return fake_config
    elif model_name == 'fake_model_A':
        return fake_base_subconfig0
    elif model_name == 'fake_model_B':
        return fake_base_subconfig1


class TestQuickRunConfigGenerator(trc.TestResultCollector):

    def setUp(self):
        fake_config = {
            "name": "fake_model_name1",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }
        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_config):
            self._mock_models = [
                ModelProfileSpec(
                    ConfigModelProfileSpec(model_name="fake_model_name"),
                    MagicMock(), MagicMock(), MagicMock())
            ]

        self._dims = SearchDimensions()
        self._dims.add_dimensions(0, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR),
            SearchDimension("concurrency",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL)
        ])

        sc = SearchConfig(dimensions=self._dims, radius=5, min_initialized=2)
        config = self._create_config()
        self._qrcg = QuickRunConfigGenerator(sc, config, MagicMock(),
                                             self._mock_models, {}, MagicMock(),
                                             ModelVariantNameManager())

    def test_get_starting_coordinate(self):
        """ Test that get_starting_coordinate() works for non-zero values """
        #yapf: disable
        dims = SearchDimensions()
        dims.add_dimensions(0, [
                SearchDimension("x", SearchDimension.DIMENSION_TYPE_EXPONENTIAL, min=2),
                SearchDimension("y", SearchDimension.DIMENSION_TYPE_LINEAR, min=1),
                SearchDimension("z", SearchDimension.DIMENSION_TYPE_EXPONENTIAL, min=3)
        ])
        sc = SearchConfig(dimensions=dims,radius=2, min_initialized=2)
        #yapf: enable
        qrcg = QuickRunConfigGenerator(sc, MagicMock(), MagicMock(),
                                       self._mock_models, {}, MagicMock(),
                                       ModelVariantNameManager())
        self.assertEqual(qrcg._get_starting_coordinate(), Coordinate([2, 1, 3]))

    def test_get_next_run_config(self):
        """
        Test that get_next_run_config() creates a proper RunConfig

        Sets up a case where the coordinate is [5,7], which cooresponds to
          - max_batch_size = 32
          - instance_count = 8
          - concurrency = 32*8*2 = 512

        Also
        - dynamic batching should be on
        - existing values from the base model config should persist if they aren't overwritten
        """
        qrcg = self._qrcg
        qrcg._coordinate_to_measure = Coordinate([5, 7])

        #yapf: disable
        fake_base_config = {
            "name": "fake_model_name",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        expected_model_config = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 8,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 32,
            'name': 'fake_model_name_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }
        #yapf: enable

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 1)
        model_config = rc.model_run_configs()[0].model_config()
        perf_config = rc.model_run_configs()[0].perf_config()

        self.assertEqual(model_config.to_dict(), expected_model_config)
        self.assertEqual(perf_config['concurrency-range'], 512)
        self.assertEqual(perf_config['batch-size'], 1)

    def test_get_next_run_config_multi_model(self):
        """
        Test that get_next_run_config() creates a proper RunConfig for multi-model

        Sets up a case where the coordinate is [1,2,4,5], which cooresponds to
          - model 1 max_batch_size = 2
          - model 1 instance_count = 3
          - model 1 concurrency = 2*3*2 = 12
          - model 2 max_batch_size = 16
          - model 2 instance_count = 6
          - model 2 concurrency = 16*6*2 = 192

        Also,
        - sequence batching should be on for model 1
        - dynamic batching should be on for model 2
        - existing values from the base model config should persist if they aren't overwritten
        - existing values for perf-analyzer config should persist if they aren't overwritten
        """

        #yapf: disable
        fake_base_config1 = {
            "name": "fake_model_name1",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4,
            "sequence_batching": {}
        }
        fake_base_config2 = {
            "name": "fake_model_name2",
            "input": [{
                "name": "INPUT__2",
                "dataType": "TYPE_FP16",
                "dims": [32]
            }],
            "max_batch_size": 8
        }

        expected_model_config1 = {
            'cpu_only': False,
            'instanceGroup': [{
                'count': 3,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 2,
            'sequenceBatching': {},
            'name': 'fake_model_name1_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }

        expected_model_config2 = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 6,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 16,
            'name': 'fake_model_name2_config_0',
            'input': [{
                "name": "INPUT__2",
                "dataType": "TYPE_FP16",
                "dims": ['32']
            }]
        }
        #yapf: enable

        mock_models = []
        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_base_config1):
            mock_models.append(
                ModelProfileSpec(
                    ConfigModelProfileSpec(
                        model_name="fake_model_name1",
                        perf_analyzer_flags={"model-version": 2}), MagicMock(),
                    MagicMock(), MagicMock()))
        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_base_config2):
            mock_models.append(
                ModelProfileSpec(
                    ConfigModelProfileSpec(
                        model_name="fake_model_name2",
                        perf_analyzer_flags={"model-version": 3}), MagicMock(),
                    MagicMock(), MagicMock()))

        dims = SearchDimensions()
        dims.add_dimensions(0, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR)
        ])
        dims.add_dimensions(1, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR)
        ])

        sc = SearchConfig(dimensions=dims, radius=5, min_initialized=2)
        config = self._create_config()
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(), mock_models, {},
                                       MagicMock(), ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([1, 2, 4, 5])

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 2)
        mc1 = rc.model_run_configs()[0].model_config()
        pc1 = rc.model_run_configs()[0].perf_config()
        mc2 = rc.model_run_configs()[1].model_config()
        pc2 = rc.model_run_configs()[1].perf_config()

        self.assertEqual(mc1.to_dict(), expected_model_config1)
        self.assertEqual(mc2.to_dict(), expected_model_config2)
        self.assertEqual(pc1['concurrency-range'], 12)
        self.assertEqual(pc1['batch-size'], 1)
        self.assertEqual(pc1['model-version'], 2)
        self.assertEqual(pc2['concurrency-range'], 192)
        self.assertEqual(pc2['batch-size'], 1)
        self.assertEqual(pc2['model-version'], 3)

    def test_default_config_generation(self):
        """
        Test that the default config is generated correctly
        """

        fake_config = {
            "name": "my-model",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        args = [
            'model-analyzer', 'profile', '--model-repository', '/tmp',
            '--config-file', '/tmp/my_config.yml'
        ]

        # yapf: disable
        yaml_str = ("""
            profile_models:
                - my-model:
                    perf_analyzer_flags:
                        percentile: 96
            """)
        # yapf: enable

        config = evaluate_mock_config(args, yaml_str, subcommand="profile")

        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_config):
            models = [
                ModelProfileSpec(spec=config.profile_models[0],
                                 config=config,
                                 client=MagicMock(),
                                 gpus=MagicMock())
            ]

        dims = SearchDimensions()
        dims.add_dimensions(0, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR),
            SearchDimension("concurrency",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL)
        ])

        sc = SearchConfig(dimensions=dims, radius=5, min_initialized=2)
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(), models, {},
                                       MagicMock(), ModelVariantNameManager())

        default_run_config = qrcg._create_default_run_config()

        self.assertTrue(
            '--percentile=96' in default_run_config.representation())

    def tearDown(self):
        patch.stopall()

    def test_default_ensemble_config_generation(self):
        """
        Test that the default ensemble config is generated correctly
        """

        fake_config = {
            "name": "my-model",
            "platform": "ensemble",
            "ensemble_scheduling": {
                "step": [{
                    "model_name": "preprocess"
                }, {
                    "model_name": "resnet50_trt"
                }]
            },
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        args = [
            'model-analyzer', 'profile', '--model-repository', '/tmp',
            '--config-file', '/tmp/my_config.yml'
        ]

        # yapf: disable
        yaml_str = ("""
            profile_models:
                - my-model:
                    perf_analyzer_flags:
                        percentile: 96
            """)
        # yapf: enable

        config = evaluate_mock_config(args, yaml_str, subcommand="profile")

        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_config):
            models = [
                ModelProfileSpec(spec=config.profile_models[0],
                                 config=config,
                                 client=MagicMock(),
                                 gpus=MagicMock())
            ]

        sc = SearchConfig(dimensions=MagicMock(), radius=5, min_initialized=2)

        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                return_value=fake_config):
            ensemble_submodels = RunConfigGeneratorFactory._create_ensemble_submodels(
                models, config, MagicMock(), MagicMock())

        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(), models,
                                       ensemble_submodels, MagicMock(),
                                       ModelVariantNameManager())

        default_run_config = qrcg._create_default_run_config()
        ensemble_subconfigs = default_run_config.model_run_configs(
        )[0].ensemble_subconfigs()

        self.assertTrue(
            "my-model_config_default" in default_run_config.representation())
        self.assertEqual(ensemble_subconfigs[0].get_field("name"),
                         "preprocess_config_default")
        self.assertEqual(ensemble_subconfigs[1].get_field("name"),
                         "resnet50_trt_config_default")

    def test_get_next_run_config_ensemble(self):
        """
        Test that get_next_run_config() creates a proper RunConfig for ensemble
        """
        self._get_next_run_config_ensemble()

    def test_get_next_run_config_ensemble_with_max_concurrency(self):
        """
        Test that get_next_run_config() creates a proper RunConfig for ensemble with a max concurrency
        """
        self._get_next_run_config_ensemble(max_concurrency=8)

    def test_get_next_run_config_ensemble_with_min_concurrency(self):
        """
        Test that get_next_run_config() creates a proper RunConfig for ensemble with a min concurrency
        """
        self._get_next_run_config_ensemble(min_concurrency=16)

    def test_get_next_run_config_max_batch_size(self):
        """
        Test that run-config-search-max-model-batch-size is enforced

        Sets up a case where the coordinate is [5,7], which corresponds to
          - max_batch_size = 32 (will be capped at 16)
          - instance_count = 8
          - concurrency = 32*8*2 = 512 (will now be 16*8*2 = 256)

        Also
        - dynamic batching should be on
        - existing values from the base model config should persist if they aren't overwritten
        """
        sc = SearchConfig(dimensions=self._dims, radius=5, min_initialized=2)
        config = self._create_config(
            additional_args=['--run-config-search-max-model-batch-size', '16'])
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(),
                                       self._mock_models, {}, MagicMock(),
                                       ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([5, 7])

        #yapf: disable
        fake_base_config = {
            "name": "fake_model_name",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        expected_model_config = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 8,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 16,
            'name': 'fake_model_name_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }
        #yapf: enable

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 1)
        model_config = rc.model_run_configs()[0].model_config()
        perf_config = rc.model_run_configs()[0].perf_config()

        self.assertEqual(model_config.to_dict(), expected_model_config)
        self.assertEqual(perf_config['concurrency-range'], 256)
        self.assertEqual(perf_config['batch-size'], 1)

    def test_get_next_run_config_max_instance_count(self):
        """
        Test that run-config-search-max-instance-count is enforced

        Sets up a case where the coordinate is [5,7], which corresponds to
          - max_batch_size = 32 
          - instance_count = 8 (will be capped at 4)
          - concurrency = 32*8*2 = 512 (will now be 32*4*2 = 256)

        Also
        - dynamic batching should be on
        - existing values from the base model config should persist if they aren't overwritten
        """
        sc = SearchConfig(dimensions=self._dims, radius=5, min_initialized=2)
        config = self._create_config(
            additional_args=['--run-config-search-max-instance-count', '4'])
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(),
                                       self._mock_models, {}, MagicMock(),
                                       ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([5, 7])

        #yapf: disable
        fake_base_config = {
            "name": "fake_model_name",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        expected_model_config = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 4,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 32,
            'name': 'fake_model_name_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }
        #yapf: enable

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 1)
        model_config = rc.model_run_configs()[0].model_config()
        perf_config = rc.model_run_configs()[0].perf_config()

        self.assertEqual(model_config.to_dict(), expected_model_config)
        self.assertEqual(perf_config['concurrency-range'], 256)
        self.assertEqual(perf_config['batch-size'], 1)

    def test_get_next_run_config_min_batch_size(self):
        """
        Test that run-config-search-min-model-batch-size is enforced

        Sets up a case where the coordinate is [5,7], which corresponds to
          - max_batch_size = 32 (will be min of 64)
          - instance_count = 8
          - concurrency = 32*8*2 = 512 (will now be 64*8*2 = 1024)

        Also
        - dynamic batching should be on
        - existing values from the base model config should persist if they aren't overwritten
        """
        sc = SearchConfig(dimensions=self._dims, radius=5, min_initialized=2)
        config = self._create_config(
            additional_args=['--run-config-search-min-model-batch-size', '64'])
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(),
                                       self._mock_models, {}, MagicMock(),
                                       ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([5, 7])

        #yapf: disable
        fake_base_config = {
            "name": "fake_model_name",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        expected_model_config = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 8,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 64,
            'name': 'fake_model_name_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }
        #yapf: enable

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 1)
        model_config = rc.model_run_configs()[0].model_config()
        perf_config = rc.model_run_configs()[0].perf_config()

        self.assertEqual(model_config.to_dict(), expected_model_config)
        self.assertEqual(perf_config['concurrency-range'], 1024)
        self.assertEqual(perf_config['batch-size'], 1)

    def test_get_next_run_config_min_instance_count(self):
        """
        Test that run-config-search-min-instance-count is enforced

        Sets up a case where the coordinate is [5,7], which corresponds to
          - max_batch_size = 32 
          - instance_count = 8 (will be min of 16)
          - concurrency = 32*8*2 = 512 (will now be 32*16*2 = 1024)

        Also
        - dynamic batching should be on
        - existing values from the base model config should persist if they aren't overwritten
        """
        sc = SearchConfig(dimensions=self._dims, radius=5, min_initialized=2)
        config = self._create_config(
            additional_args=['--run-config-search-min-instance-count', '16'])
        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(),
                                       self._mock_models, {}, MagicMock(),
                                       ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([5, 7])

        #yapf: disable
        fake_base_config = {
            "name": "fake_model_name",
            "input": [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": [16]
            }],
            "max_batch_size": 4
        }

        expected_model_config = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 16,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 32,
            'name': 'fake_model_name_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }
        #yapf: enable

        rc = qrcg._get_next_run_config()

        self.assertEqual(len(rc.model_run_configs()), 1)
        model_config = rc.model_run_configs()[0].model_config()
        perf_config = rc.model_run_configs()[0].perf_config()

        self.assertEqual(model_config.to_dict(), expected_model_config)
        self.assertEqual(perf_config['concurrency-range'], 1024)
        self.assertEqual(perf_config['batch-size'], 1)

    def _get_next_run_config_ensemble(self,
                                      max_concurrency=0,
                                      min_concurrency=0):
        """
        Test that get_next_run_config() creates a proper RunConfig for ensemble

        Sets up a case where the coordinate is [1,2,4,5], which cooresponds to
          - submodel 1 max_batch_size = 2
          - submodel 1 instance_count = 3
          - submodel 1 concurrency = 2*3*2 = 12
          - submodel 2 max_batch_size = 16
          - submodel 2 instance_count = 6
          - submodel 2 concurrency = 16*6*2 = 192
          - ensemble model concurrency = 12 (minimum value of [12, 192])

        Also,
        - sequence batching should be on for model 1
        - dynamic batching should be on for model 2
        - existing values from the base model config should persist if they aren't overwritten
        - existing values for perf-analyzer config should persist if they aren't overwritten
        """

        additional_args = []
        if max_concurrency:
            additional_args.append('--run-config-search-max-concurrency')
            additional_args.append(f'{max_concurrency}')
        if min_concurrency:
            additional_args.append('--run-config-search-min-concurrency')
            additional_args.append(f'{min_concurrency}')

        #yapf: disable
        expected_model_config0 = {
            'cpu_only': False,
            'instanceGroup': [{
                'count': 3,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 2,
            'sequenceBatching': {},
            'name': 'fake_model_A_config_0',
            'input': [{
                "name": "INPUT__0",
                "dataType": "TYPE_FP32",
                "dims": ['16']
            }]
        }

        expected_model_config1 = {
            'cpu_only': False,
            'dynamicBatching': {},
            'instanceGroup': [{
                'count': 6,
                'kind': 'KIND_GPU',
            }],
            'maxBatchSize': 16,
            'name': 'fake_model_B_config_0',
            'input': [{
                "name": "INPUT__2",
                "dataType": "TYPE_FP16",
                "dims": ['32']
            }]
        }
        #yapf: enable

        config = self._create_config(additional_args)

        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                side_effect=mock_ensemble_configs):
            models = [
                ModelProfileSpec(spec=config.profile_models[0],
                                 config=config,
                                 client=MagicMock(),
                                 gpus=MagicMock())
            ]

        with patch(
                "model_analyzer.triton.model.model_config.ModelConfig.create_model_config_dict",
                side_effect=mock_ensemble_configs):
            ensemble_submodels = RunConfigGeneratorFactory._create_ensemble_submodels(
                models, config, MagicMock(), MagicMock())

        dims = SearchDimensions()
        dims.add_dimensions(0, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR)
        ])
        dims.add_dimensions(1, [
            SearchDimension("max_batch_size",
                            SearchDimension.DIMENSION_TYPE_EXPONENTIAL),
            SearchDimension("instance_count",
                            SearchDimension.DIMENSION_TYPE_LINEAR)
        ])

        sc = SearchConfig(dimensions=dims, radius=5, min_initialized=2)

        qrcg = QuickRunConfigGenerator(sc, config, MagicMock(), models,
                                       ensemble_submodels, MagicMock(),
                                       ModelVariantNameManager())

        qrcg._coordinate_to_measure = Coordinate([1, 2, 4, 5])

        run_config = qrcg._get_next_run_config()

        self.assertEqual(len(run_config.model_run_configs()), 1)
        self.assertEqual(
            len(run_config.model_run_configs()[0].ensemble_subconfigs()), 2)

        model_config = run_config.model_run_configs()[0].model_config()
        perf_config = run_config.model_run_configs()[0].perf_config()
        submodel_config0 = run_config.model_run_configs(
        )[0].ensemble_subconfigs()[0]
        submodel_config1 = run_config.model_run_configs(
        )[0].ensemble_subconfigs()[1]

        self.assertEqual(submodel_config0.to_dict(), expected_model_config0)
        self.assertEqual(submodel_config1.to_dict(), expected_model_config1)

        if max_concurrency:
            self.assertEqual(perf_config['concurrency-range'], max_concurrency)
        elif min_concurrency:
            self.assertEqual(perf_config['concurrency-range'], min_concurrency)
        else:
            self.assertEqual(perf_config['concurrency-range'], 12)

        self.assertEqual(perf_config['batch-size'], 1)

    def _create_config(self, additional_args=[]):
        args = [
            'model-analyzer', 'profile', '--model-repository', '/tmp',
            '--config-file', '/tmp/my_config.yml'
        ]

        for arg in additional_args:
            args.append(arg)

        # yapf: disable
        yaml_str = ("""
            profile_models:
                - my-model
            """)
        # yapf: enable

        config = evaluate_mock_config(args, yaml_str, subcommand="profile")

        return config

    def tearDown(self):
        patch.stopall()


if __name__ == "__main__":
    unittest.main()
