from __future__ import annotations

import unittest
from pathlib import Path

from slam_benchmark.algorithms.contracts import (
    get_algorithm_contract,
    supported_algorithm_ids,
)


class AlgorithmContractTests(unittest.TestCase):
    def test_orbslam3_mono_inertial_euroc_contract(self) -> None:
        contract = get_algorithm_contract("orbslam3_mono_inertial_euroc")

        self.assertEqual(contract.algorithm_id, "orbslam3_mono_inertial_euroc")
        self.assertEqual(contract.display_name, "ORB-SLAM3 Mono-Inertial (EuRoC)")
        self.assertEqual(contract.contract_version, 1)
        self.assertEqual(
            contract.entrypoint_relative_path,
            Path("Examples/Monocular-Inertial/mono_inertial_euroc"),
        )
        self.assertEqual(contract.fixed_output_relative_path, Path("f_vo.txt"))
        self.assertEqual(contract.supported_dataset_types, ("euroc",))
        self.assertIn(contract.algorithm_id, supported_algorithm_ids())


if __name__ == "__main__":
    unittest.main()
