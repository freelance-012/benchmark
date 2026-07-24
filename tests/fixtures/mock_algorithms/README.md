# Mock algorithms

这三个目录是后续构建和运行模块使用的测试夹具，不属于正式算法。
它们不会解析传感器内容，只记录 Pipeline 实际传入的参数，用于核对算法入口、
数据集输入映射、Segment 起止时间戳、工作目录和固定输出是否正确。

| 测试算法 | 对应数据集类型 | 编译入口 | 编译产物 | 固定输出 |
| --- | --- | --- | --- | --- |
| `algorithm1` | RK3588、RK3399 | `build.sh` | `build/algorithm1` | `mock_output.txt` |
| `algorithm2` | RK3399 | `build.sh` | `build/algorithm2` | `mock_output.txt`、`home_point.txt` |
| `algorithm3` | KITTI | `build.sh` | `build/algorithm3` | `mock_output.txt` |

每个编译脚本都要求以对应算法目录作为工作目录，并使用系统 `cc` 编译 `main.c`。
三个运行入口的前三个参数统一为数据集根路径、Segment 起点和 Segment 终点，
后续位置参数由各自的数据集类型决定。

- `algorithm1` 模拟 VO；RK3588 接收 IMU、四路视频、两份图像时间戳和两份标定路径，RK3399 接收 IMU、视频、图像时间戳和标定路径。
- `algorithm2` 接收 RK3399 的 IMU、视频、图像时间戳和标定路径。
- `algorithm3` 接收 KITTI 的时间戳、标定、左右图像目录，以及可选真值路径。

三个程序都将相同格式的键值记录打印到标准输出，并覆盖写入算法工作目录下的
`mock_output.txt`。测试会逐行比较标准输出、固定输出和预期参数，差异即表示
Pipeline 的输入映射或工作目录处理存在问题。`algorithm2` 还会同时生成
`home_point.txt`，用于验证 VLOC 的轨迹和 home point 都属于算法输出。

夹具目录本身不是独立 Git 仓库。需要测试 Git 记录时，应先复制单个算法目录到
临时位置，再在副本中初始化 Git，避免在主仓库中嵌套 `.git`。
