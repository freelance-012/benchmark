# SLAM Benchmark

本仓库正在实现一个本地、单用户、CLI 启动的 SLAM 算法基线测试系统。当前代码已完成数据集管理、算法编译和算法运行模块，覆盖 RK3399、RK3588、KITTI Odometry 数据集，以及可记录 Git、构建回执、逐 Segment 运行结果和数据集级检查点的串行执行流程。

## 当前范围

已实现：

- 从用户给定的总根目录递归发现数据集；
- 按 RK3399、RK3588、KITTI Odometry 内置契约定位固定输入；
- 按 voeval 的固定 21 列顺序校验 `imu.txt`；
- 由数据集类型契约选择固定分段规则；RK3399、RK3588 使用 `flight_mode`，KITTI 使用时间戳首尾范围；
- 使用所选数据集类型约定的图像时间戳统计每个 Segment 的输入图像帧数；
- 将同时达到 200 帧和 10 秒的 Segment 标记为有效；
- 在每个具体数据集根目录原子写入 `benchmark_dataset.yaml`；
- 查询已录入的数据集和 Segment；
- 读取最小算法编译配置并校验算法 Git 仓库和编译脚本；
- 在独立进程组中执行编译脚本，保存标准输出、错误输出和退出码；
- 校验算法内置契约绑定的编译后运行入口；
- 记录编译前后 Git 状态，并在 HEAD 或已跟踪源码发生变化时拒绝继续；
- 原子保存 `build_receipt.yaml`；
- 根据算法内置契约自动组合数据集路径、Segment 起止时间戳和固定输入路径；
- 串行运行每个有效 Segment，不生成新的运行脚本，也不通过 shell 解析参数；
- 校验模拟算法固定输出并保存到当前数字 Segment 目录；
- 保存 Segment 回执、日志、冻结配置和数据集级检查点；
- 默认跳过问题数据集并继续，也可使用 `--fail-fast` 在第一次失败时退出；
- 在上下文未变化时，从未完成的数据集恢复运行。

暂未实现 EuRoC、voeval 自动评估、Excel 汇总、回归对比和最终报告。这些能力保留在总体设计中，后续按模块接入。

## 项目结构

```text
benchmark/
├── configs/                  # 用户配置示例；本机配置不会提交
├── docs/                     # 原有需求、调研、HLD、LLD 与文档模板
├── src/slam_benchmark/       # Pipeline 源码
│   ├── algorithms/           # 框架维护的算法内置契约
│   ├── compilation/          # 算法编译、Git 快照、回执与日志
│   ├── datasets/             # 数据集契约、扫描、分段与存储
│   └── execution/            # 命令组合、算法执行、输出校验与恢复
├── tests/                    # 单元测试和三个可编译模拟算法
├── tools/                    # 可重复生成和验证异常测试数据的工具
├── pyproject.toml            # Python 包与依赖定义
└── README.md
```

文档保持原有目录不变：[`docs/srs/srs.md`](docs/srs/srs.md)、[`docs/research/research.md`](docs/research/research.md)、[`docs/hld/hld.md`](docs/hld/hld.md) 和 [`docs/lld/lld.md`](docs/lld/lld.md)。

## 环境与依赖

- Python 3.8 或更高版本；
- PyYAML 6.x。

使用系统 Python 安装到当前用户目录，不创建或激活虚拟环境：

```bash
python3 -m pip install --user --break-system-packages -e .
export PATH="${HOME}/.local/bin:${PATH}"
benchmark --help
```

`--user` 将 Python 包和 `benchmark` 命令安装在当前用户目录。Ubuntu、Debian
等启用了外部管理保护的系统需要 `--break-system-packages`；其他系统的 pip
不支持该参数时可以将它去掉。

只安装运行依赖时使用：

```bash
python3 -m pip install --user --break-system-packages -r requirements.txt
```

测试使用 Python 标准库 `unittest`，没有额外测试依赖。

需要运行代码风格检查时安装开发依赖：

```bash
python3 -m pip install --user --break-system-packages -r requirements-dev.txt
```

## 数据集配置

用户只填写数据集总根目录和本次扫描的数据集类型：

```yaml
dataset:
  root_path: /path/to/benchmark_datasets
  type: RK3399
```

示例见 `configs/dataset.example.yaml`。一次扫描只处理一种数据集类型。

KITTI Odometry 推荐将包含 `sequences/` 和可选 `poses/` 的数据包根目录作为 `root_path`：

```yaml
dataset:
  root_path: /path/to/kitti_odometry
  type: KITTI
```

## 内置数据集契约

RK3399 必需文件：

- `imu.txt`
- `img.avi`
- `imgts.txt`
- `calib_raw.yaml`

RK3588 必需文件：

- `imu.txt`
- `video_bottom_0.h265`
- `video_bottom_1.h265`
- `video_front_0.h265`
- `video_front_1.h265`
- `imgts_bottom.txt`
- `imgts_front.txt`
- `bottom_calib_raw.yaml`
- `front_calib_raw.yaml`

KITTI Odometry 每个 `sequences/XX` 序列必须包含：

- `times.txt`
- `calib.txt`
- 灰度双目目录 `image_0/`、`image_1/`，或者彩色双目目录 `image_2/`、`image_3/`

KITTI 左右图像文件名必须一致，并从 `000000.png` 连续编号；图像对数量必须与 `times.txt` 完全一致。灰度和彩色目录同时存在时优先使用灰度双目。训练序列的 `poses/XX.txt` 会作为可选真值输入校验并记录；缺少或无效时数据仍可运行，但会给出不能进行真值评估的警告。每个 KITTI 序列使用 `times.txt` 的第一条和最后一条时间戳形成一个 Segment。

RK3588 的前视和下视时间戳必须完全一致。系统同时校验两份文件，但只按一份同步时间戳计数，不把四路视频的帧数相加。当前 RK3399 数据处理器版本为 3，RK3588 为 4；旧实例 YAML 会在重新扫描时按新契约重建。

数据集中的 `home_point.txt` 只作为可选数据记录，不再作为 VLOC 的运行或评估输入。VLOC 必须和轨迹一起输出本次运行自己的 `home_point.txt`。

扫描过程不会修改 IMU、图像、时间戳或标定文件。系统只在扫描识别出的每个具体数据集根目录生成一份 `benchmark_dataset.yaml`，其中保存该数据集的输入路径和 Segment；算法适用性由系统根据输入文件和算法契约判断，不写入数据集配置。

## 算法编译配置

用户只选择算法，并提供算法 Git 仓库根路径和仓库内的可执行编译脚本：

```yaml
algorithm: algorithm1

build:
  algorithm_path: /absolute/path/to/algorithm1
  script_path: /absolute/path/to/algorithm1/build.sh
```

通用模拟算法示例见 `configs/algorithm.example.yaml`。工作目录固定为
`build.algorithm_path`；运行入口由算法内置契约确定。当前
`algorithm1` 是兼容 RK3588 和 RK3399 的 `sf_vo` 模拟算法；
`algorithm2` 是 RK3399 的 `sf_vloc` 模拟算法；`algorithm3` 是 KITTI
模拟算法，暂未绑定 voeval 工作流。正式算法后续以相同契约接入。

ORB-SLAM3 EuRoC 单目惯性编译使用
`configs/orbslam3.example.yaml`，将其中两个路径替换为本机 ORB-SLAM3 Git
仓库根目录和仓库内的 `build.sh`：

```yaml
algorithm: orbslam3_mono_inertial_euroc

build:
  algorithm_path: /absolute/path/to/ORB_SLAM3
  script_path: /absolute/path/to/ORB_SLAM3/build.sh
```

该契约校验编译产物
`Examples/Monocular-Inertial/mono_inertial_euroc`。当前接入范围仅为编译；
EuRoC 数据集扫描和算法运行尚未实现。

## CLI

首次扫描并保存各数据集实例 YAML：

```bash
benchmark dataset scan --config configs/dataset.example.yaml
```

只校验、不写文件：

```bash
benchmark dataset scan --config configs/dataset.example.yaml --refresh --dry-run
```

原始数据发生变化后显式重新录入：

```bash
benchmark dataset scan --config configs/dataset.example.yaml --refresh
```

查看已录入数据集：

```bash
benchmark dataset list --config configs/dataset.example.yaml
```

独立执行一次算法编译：

```bash
benchmark build --config /path/to/algorithm.yaml
```

编译并运行一个数据集配置中的全部 READY 数据集：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml
```

只运行指定数据集目录或子树：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --dataset-path "/path/to/selected/dataset"
```

默认模式遇到问题数据集时记录失败、跳过该数据集剩余 Segment，并继续下一个数据集。需要人工调试时使用首次失败立即退出模式：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --fail-fast
```

默认算法失败阈值为 1，可以在本次运行中覆盖：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --failure-threshold 0
```

`--fail-fast` 不等待失败阈值，第一次数据集或算法运行失败就保存当前事实并返回非零退出码。用户主动按下 `Ctrl+C` 时，两种模式都会停止。

上下文未变化时，可以从结果目录记录的未完成数据集恢复：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --fail-fast \
  --resume /path/to/result/ALGORITHM_ID/TEST_ID
```

系统读取当前 commit，并在当前算法目录下自动分配下一个 `test_id`。完整 commit 保存在回执和冻结配置中，不作为目录层级。编译产物保留在算法仓库中，默认结果结构为：

```text
result/
└── ALGORITHM_ID/
    └── TEST_ID/
        ├── build_receipt.yaml
        └── logs/
            ├── build.stdout.log
            └── build.stderr.log
```

需要单独验证存储位置时，仍可使用高级参数 `--result-dir /path/to/build-result` 覆盖自动分配。

脚本退出码为 0 后，系统仍会检查内置运行入口是否存在且可执行。编译生成的未跟踪文件不视为源码变化；HEAD、分支、已跟踪文件、编译脚本或子模块在编译期间发生变化时，构建回执记为失败。

完整运行默认结果结构为：

```text
result/
└── ALGORITHM_ID/
    └── TEST_ID/
        ├── config/
        ├── logs/
        │   ├── build.stdout.log
        │   └── build.stderr.log
        ├── build_receipt.yaml
        ├── checkpoint.yaml
        └── dataset/
            ├── 0/
            │   ├── receipt.yaml
            │   ├── stdout.log
            │   ├── stderr.log
            │   ├── FIXED_OUTPUT
            │   ├── CALIBRATION_FILE
            │   ├── home_point.txt          # 仅 sf_vloc
            │   └── evaluation/
            │       ├── metrics.json
            │       ├── receipt.yaml
            │       └── voeval.log
            ├── 1/
            └── ...
```

当前运行模块会预先创建 `evaluation/`，但不会伪造评估文件；等 voeval
评估模块接入后，才会在其中写入真实的 `metrics.json`、`receipt.yaml`
和 `voeval.log`。

运行成功后，系统按数据集契约把 voeval 使用的单份外参复制到
当前数字 Segment 目录：RK3399 使用 `calib_raw.yaml`，RK3588 使用
`bottom_calib_raw.yaml`。`sf_vloc` 的 `vloc.txt` 和 `home_point.txt`
均由算法产生，不从数据集复制。
RK3588 的 `front_calib_raw.yaml` 不进入评估目录。

所有有效 Segment 按本次 run 的稳定顺序从 0 开始预先编号；实际启动过的 Segment 创建对应数字目录，因当前数据集失败而跳过的 Segment 保留编号但不创建虚假结果，所以异常运行中可能出现编号空缺。目录层级不再按数据集分组，但每个 `receipt.yaml` 仍记录原始数据集、Segment 和起止时间戳。数据集运行结果集中保存在 `checkpoint.yaml`，不再生成 `dataset_receipt.yaml`。

恢复未完成的数据集时，仅清理该数据集尚未提交检查点的数字 Segment 目录并从该数据集起点重新运行；之前已经提交完成的数据集不重复运行。

不安装本项目且不使用虚拟环境时，系统 Python 必须已经能够导入 PyYAML：

```bash
python3 -c "import yaml; print(yaml.__version__)"
```

然后从仓库根目录通过 `PYTHONPATH=src` 运行。例如扫描数据集：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m slam_benchmark dataset scan \
  --config configs/dataset.example.yaml
```

不安装本项目、也不使用虚拟环境时编译 ORB-SLAM3：

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
  python3 -m slam_benchmark build \
  --config configs/orbslam3.local.yaml
```

## 分段与有效性

- 每个数据集类型在内置契约中绑定一种分段规则；
- `flight_mode` 规则根据飞行状态产生一个或多个候选 Segment；
- `timestamp` 规则将第一条到最后一条有效时间戳作为一个候选 Segment；
- `imu.txt` 第 1 列固定为时间戳，第 4 列固定为 `flight_mode`；
- `imu.txt`、`imgts_bottom.txt`、`imgts_front.txt` 的最后一行不参与解析；RK3399 的 `imgts.txt` 仍完整读取；
- `flight_mode` 从 0 进入非 0 时开始 Segment；
- 连续非 0 状态属于同一个 Segment，即使状态值发生变化；
- 遇到 0 时结束，参与读取的记录末尾仍为非 0 时使用最后一条非 0 记录结束；
- 起点和终点使用有效飞行记录的时间戳；
- RK3399 使用 `imgts.txt` 计数；RK3588 校验 `imgts_bottom.txt` 和 `imgts_front.txt` 一致后，使用该同步时间戳序列计数；
- KITTI 使用 `times.txt` 计数，并要求时间戳严格递增且与左右图像对一一对应；
- 输入图像帧数不少于 200 且持续时间不少于 10 秒时 Segment 有效。

这里的 200 帧/10 秒是运行前的数据集输入检查。算法运行后，voeval 对 `vo.txt` 执行的 reset 分段和输出轨迹过滤仍然独立生效。

## 测试

```bash
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
ruff check src tests tools
ruff format --check src tests tools
```

`tests/fixtures/mock_algorithms/` 中的三个模拟算法会在临时 Git 仓库内编译，不会在源码目录留下构建产物。编译模块测试覆盖成功、非零退出、入口缺失、超时、路径越界、脚本不可执行、HEAD 变化和已跟踪源码变化。运行模块测试覆盖三个数据集类型、带空格路径、固定输入映射、默认跳过、`--fail-fast`、数据集选择、缺失输出、超时和检查点恢复。

### 数据集异常识别套件

异常测试数据不保存在仓库中。工具默认在系统临时目录生成 RK3399、RK3588、KITTI、已有实例 YAML 恢复及非法用户配置案例：

```bash
python3 tools/generate_dataset_anomaly_suite.py
python3 tools/verify_dataset_anomaly_suite.py
python3 tools/generate_dataset_anomaly_suite.py --clean
```

测试数据中的视频是最小占位文件，只用于数据集管理校验，不能用于算法运行。生成、验证、清理完成后，源码仓库不会留下测试数据目录。
