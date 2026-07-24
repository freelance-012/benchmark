from __future__ import annotations

import unittest
from pathlib import Path

from slam_benchmark.algorithms.contracts import (
    get_algorithm_contract,
    supported_algorithm_ids,
)


class AlgorithmContractTests(unittest.TestCase):
    def test_algorithm1_supports_rk3588_and_rk3399_vo_inputs(self) -> None:
        contract = get_algorithm_contract("algorithm1")

        self.assertEqual(contract.display_name, "Mock SF VO Algorithm")
        self.assertEqual(contract.contract_version, 5)
        self.assertEqual(contract.evaluation_workflow, "sf_vo")
        self.assertEqual(contract.supported_dataset_types, ("rk3588", "rk3399"))
        self.assertEqual(
            contract.run_contract_for("rk3588").required_input_roles,
            (
                "imu_path",
                "bottom_video_0_path",
                "bottom_video_1_path",
                "front_video_0_path",
                "front_video_1_path",
                "bottom_image_timestamps_path",
                "front_image_timestamps_path",
                "bottom_calibration_path",
                "front_calibration_path",
            ),
        )
        self.assertEqual(
            contract.run_contract_for("rk3399").required_input_roles,
            (
                "imu_path",
                "image_path",
                "image_timestamps_path",
                "calibration_path",
            ),
        )

    def test_orbslam3_mono_inertial_euroc_contract(self) -> None:
        contract = get_algorithm_contract("orbslam3_mono_inertial_euroc")

        self.assertEqual(contract.algorithm_id, "orbslam3_mono_inertial_euroc")
        self.assertEqual(contract.display_name, "ORB-SLAM3 Mono-Inertial (EuRoC)")
        self.assertEqual(contract.contract_version, 2)
        self.assertEqual(
            contract.entrypoint_relative_path,
            Path("Examples/Monocular-Inertial/mono_inertial_euroc"),
        )
        self.assertEqual(contract.fixed_output_relative_path, Path("f_vo.txt"))
        self.assertEqual(contract.output_relative_paths, (Path("f_vo.txt"),))
        self.assertEqual(contract.supported_dataset_types, ("euroc",))
        self.assertIn(contract.algorithm_id, supported_algorithm_ids())


if __name__ == "__main__":
    unittest.main()
