# SLAM Benchmark

本仓库正在实现一个本地、单用户、CLI 启动的 SLAM 算法基线测试系统。当前代码已完成数据集管理、算法编译、算法运行和逐 Segment 评估模块，覆盖 RK3399、RK3588、KITTI Odometry 数据集，以及可记录 Git、构建回执、逐 Segment 运行与评估结果、Excel 汇总和数据集级检查点的串行执行流程。

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
- 默认根据算法内置契约组合位置参数，也可由算法配置覆盖运行命令模板；
- 串行运行每个有效 Segment，不生成新的运行脚本，也不通过 shell 解析参数；
- 校验模拟算法固定输出并保存到当前数字 Segment 目录；
- 保存 Segment 回执、日志、冻结配置和数据集级检查点；
- 默认记录失败 Segment 并继续后续 Segment，也可使用 `--fail-fast` 在第一次失败时退出；
- 在上下文未变化时，从未完成的数据集恢复运行；
- 对绑定 SF VO 或 SF VLOC 评估工作流且运行成功的 Segment，立即调用用户环境中的 `voeval`；
- 每次评估保存评估回执和 `voeval.log`；评估成功时保证 `metrics.json` 存在且有效，评估失败时不保证该文件存在或有效；
- 仅对绑定评估工作流的算法，在每个 Segment 形成状态后重建本次测试的单表 `run_summary.xlsx`；
- 支持对已有测试结果重新执行 voeval 评估并生成带时间戳的报告；
- 在交互式终端显示各模块进度，并结合 Segment 时长和算法实时输出估算剩余时间。

当前已注册真实 SF VO 输出契约 `orbslam3_mono_sf`；SF VLOC 的评估和汇总接口已经实现，但目前只有模拟算法用于验证流程，尚未接入真实 VLOC 算法。暂未实现 EuRoC 运行、回归对比和最终报告。

## 项目结构

```text
benchmark/
├── configs/                  # 用户配置示例；本机配置不会提交
├── docs/                     # 原有需求、调研、HLD、LLD 与文档模板
├── src/slam_benchmark/       # Pipeline 源码
│   ├── algorithms/           # 框架维护的算法内置契约
│   ├── compilation/          # 算法编译、Git 快照、回执与日志
│   ├── datasets/             # 数据集契约、扫描、分段与存储
│   ├── evaluation/           # voeval 调用、评估回执与 Excel 汇总
│   └── execution/            # 命令组合、算法执行、输出校验与恢复
├── tests/                    # 单元测试和三个可编译模拟算法
├── tools/                    # 可重复生成和验证异常测试数据的工具
├── pyproject.toml            # Python 包与依赖定义
└── README.md
```

文档保持原有目录不变：[`docs/srs/srs.md`](docs/srs/srs.md)、[`docs/research/research.md`](docs/research/research.md)、[`docs/hld/hld.md`](docs/hld/hld.md) 和 [`docs/lld/lld.md`](docs/lld/lld.md)。

## 环境与依赖

- Python 3.8 或更高版本；
- PyYAML 6.x；
- openpyxl 3.1.x；
- Rich 13.7 至 14.x；
- 用户环境中可直接执行的 `voeval` 命令。

使用系统 Python 安装到当前用户目录，不创建或激活虚拟环境：

```bash
python3 -m pip install --user -e .
export PATH="${HOME}/.local/bin:${PATH}"
benchmark --help
```

`--user` 将 Python 包和 `benchmark` 命令安装在当前用户目录。

**注意：** 如果你的系统使用 PEP 668 外部管理保护（如 Ubuntu 23.04+、Debian 12+），且 pip 版本 >= 23.0.1，需要添加 `--break-system-packages` 参数：

```bash
python3 -m pip install --user --break-system-packages -e .
```

可以通过 `python3 -m pip --version` 检查 pip 版本。如果版本低于 23.0.1，则不需要 `--break-system-packages` 参数。

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

RK3399 在正式扫描或运行前，会把每个具体数据集目录中的
`calib_raw.yaml` 复制为同目录下的 `calib.yaml`。如果 `calib.yaml`
已经存在也会覆盖，确保它始终和原始标定一致；`calib_raw.yaml` 本身不修改。
`--dry-run` 和 `dataset list` 不执行该复制。

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

数据集管理不会录入或使用数据集中的 `home_point.txt`。VLOC 必须和轨迹一起输出本次运行自己的 `home_point.txt`。

除上述 RK3399 的 `calib.yaml` 兼容副本外，扫描过程不会修改 IMU、图像、
时间戳或原始标定文件。系统只在扫描识别出的每个具体数据集根目录生成一份
`benchmark_dataset.yaml`，其中保存该数据集的输入路径和 Segment；算法
适用性由系统根据输入文件和算法契约判断，不写入数据集配置。

## 算法编译配置

用户只选择算法，并提供算法 Git 仓库根路径和仓库内的可执行编译脚本：

```yaml
algorithm: algorithm1

build:
  algorithm_path: /absolute/path/to/algorithm1
  script_path: /absolute/path/to/algorithm1/build.sh

run:
  output_root_path: /absolute/path/to/algorithm1_output
```

`algorithm1` 使用外部编号输出：算法在 `output_root_path` 下维护
`log_count.txt` 和 `0/`、`1/` 等编号目录。`log_count.txt` 只保存当前
编号，例如 `10`，不包含 YAML 字段或其他内容。Pipeline 对比运行前后的计数器，
只从本次新增的编号目录复制契约规定的输出，并将计数器快照保存到 Segment
结果中的 `log_count.txt`。没有外部编号输出的算法不配置这个路径，
继续从算法工作目录读取固定输出。

已有输出目录需要进行一次迁移：把旧 `counter.yaml` 中的
`last_completed: N` 转换为只包含 `N` 的 `log_count.txt`，并删除旧文件。
Pipeline 不再读取 YAML 计数器，也不会根据现有数字目录猜测当前编号。

默认运行命令继续使用算法内置契约规定的位置参数顺序。只有算法要求不同的参数
前缀或排列时，才增加可选的 `run.command_template`：

```yaml
run:
  command_template: >-
    "{executable}" --log={dataset_path}
    --start_time={start_ts:.3f} --end_time={end_ts:.3f}
```

模板必须以 `{executable}` 开头，也可以改写为等价的 YAML 参数列表。可使用
`{dataset_path}`、`{start_ts}`、`{end_ts}` 以及算法契约声明的输入角色，
例如 `{imu_path}`。Pipeline 会先将模板规范化成参数列表，再直接执行，不通过
shell 解析。模板引用的可选输入不存在时，对应参数值为 `<none>`。

通用模拟算法示例见 `configs/algorithm.example.yaml`。工作目录固定为
`build.algorithm_path`；运行入口由算法内置契约确定。当前
`algorithm1` 是兼容 RK3588 和 RK3399 的 `sf_vo` 模拟算法；
`algorithm2` 是 RK3399 的 `sf_vloc` 模拟算法；`algorithm3` 是 KITTI
模拟算法，暂未绑定 voeval 工作流。`algorithm2` 只用于验证 VLOC 调用、
双输出和汇总流程，输出的是 `mock_output.txt` 和 `home_point.txt`，不能代替
真实算法所需的 `vloc.txt`。正式算法后续以相同契约接入。

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

ORB-SLAM3 单目模式运行 RK3399 SF VO 数据使用
`configs/orbslam3_mono_sf.example.yaml`。构建脚本和 RK3399 输入实现均属于
ORB-SLAM3 仓库，benchmark 只使用通用算法构建和运行接口：

```yaml
algorithm: orbslam3_mono_sf

build:
  algorithm_path: /absolute/path/to/ORB_SLAM3
  script_path: /absolute/path/to/ORB_SLAM3/local_scripts/build_mono_sf.sh
```

该契约固定要求 RK3399 的 `imu.txt`、`img.avi`、`imgts.txt` 和
`calib_raw.yaml`。benchmark 按标准接口直接调用 ORB-SLAM3：

```text
mono_sf DATASET_ROOT SEGMENT_START SEGMENT_END IMU_PATH IMG_AVI_PATH IMGTS_PATH CALIB_RAW_PATH
```

`mono_sf` 内部完成以下工作：

- 校验四个 RK3399 输入并从 `calib_raw.yaml` 的 `cam1` 生成临时相机设置；
- 直接读取原始 `img.avi` 和 `imgts.txt`，按 Segment 起止时间选择帧；
- 使用下视图像的下半幅进行单目跟踪，IMU 输入仅按接口校验、不参与跟踪；
- 从算法仓库内部解析 ORB 词典路径并生成固定输出 `vo.txt`。

临时相机设置和内部轨迹文件在运行结束后自动清理，原始数据集不会被修改。
benchmark 不包含 ORB-SLAM3 专用输入、运行或构建适配器。

`orbslam3_mono_sf` 固定输出 `vo.txt`，每一行必须严格包含 voeval `sf_vo`
使用的 11 列：

```text
timestamp num_inliers x y z yaw pitch roll is_keyframe time_cost reset_count
```

时间戳单位为秒，`yaw/pitch/roll` 单位为度，`time_cost` 单位为毫秒。
benchmark 会在保存结果前校验列数、数值、时间范围、时间顺序和整数状态列，
成功后再将 `vo.txt` 与 RK3399 的 `calib_raw.yaml` 一起复制到数字 Segment
目录。默认开启 ORB Viewer；无界面运行时可在命令前设置：

```bash
SLAM_BENCHMARK_ORB_VIEWER=0 benchmark run ...
```

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

### Debug 模式

在任意现有命令中加入 `--debug` 即可开启，例如：

```bash
benchmark dataset scan --config configs/dataset.example.yaml --debug

benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --debug
```

Debug 输出写到终端的标准错误流，不改变原有命令结果。终端只显示数据集、
编译、运行、评估和汇总主要模块，不展开 Git 查询、配置读取、文件复制和校验
等内部步骤。每个模块只保留 Pipeline 最终组装的输入命令或输入文件、实际
标准输出与错误输出（如有）、执行状态，以及本次真正保存的结果文件。

### 运行进度与预计剩余时间

在交互式终端执行 `benchmark run` 时，会持续显示总进度以及 `DATASET`、
`BUILD`、`RUN`、`EVALUATE`、`REPORT` 五个模块的进度条。运行、评估和汇总
按 Segment 分别推进；只有一个 Segment 的结果、评估和汇总均已保存后，
总进度才会增加。

`RUN` 开始时，系统根据本次全部 Segment 的起止时间戳计算待处理数据时长，
初始按 1× 速度（1 秒数据约需 1 秒运行时间）估算“预计剩余”。算法运行期间，
Pipeline 同时将标准输出和错误输出写入原有日志；如果当前算法契约绑定了进度
解析器，还会解析当前时间戳、百分比、已处理帧数或 FPS，并据此更新当前 Segment
百分比、处理速度和预计剩余时间。一个 Segment 成功结束后，实际数据时长与
实际运行耗时还会用于修正后续 Segment 的估算。没有绑定解析器或没有可解析
进度输出时，系统继续使用数据时长和已经完成的 Segment 速度估算，不影响算法
运行。

运行中的模块显示“预计剩余”；完成、失败、中断或跳过后不再显示剩余时间，
实际耗时由耗时列保留。多路同步视频按同一个 Segment 时间范围计算，不将多路
视频时长相加。`EVALUATE` 和 `REPORT` 只在实际评估或更新汇总时显示“运行”并
累计耗时，等待下一段运行结果时显示“等待”，等待时间不计入模块耗时。

算法需要实时更新进度时，由算法或算法适配入口逐行输出约定的进度事件，例如：

```text
BENCHMARK_PROGRESS {"timestamp":125.0,"percent":25.0,"fps":20.0}
```

输出行必须及时刷新，不能一直缓存在算法进程内部。进度解析器由算法内置契约
绑定，不需要用户在运行 YAML 中增加配置；普通日志和无法识别的输出仍照常保存，
不会导致本次算法运行失败。用户仍使用原有的 `benchmark run` 命令，无需增加
进度或预计剩余时间参数。当前只有模拟算法 `algorithm1` 绑定了实时进度解析器；
其他算法使用 Segment 时长和已完成 Segment 的实际速度估算。

Debug 模式和非交互式输出不会显示动态进度条，避免进度刷新字符写入日志。
命令结束后的成功或失败汇总保持不变。

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

SF VO 的 RPE 间隔默认是 `100 m`。可以在运行时填写其他数值和单位：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --rpe-delta 50 \
  --rpe-unit m
```

`--rpe-unit` 支持 `m`（米）和 `f`（帧）。米制间隔可以使用正数或小数，
例如 `25.5 m`；帧间隔必须是正整数，例如
`--rpe-delta 200 --rpe-unit f`。该数值会同时用于 voeval 评估、指标校验和
Excel 汇总表头。

只运行指定数据集目录或子树：

```bash
benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --dataset-path "/path/to/selected/dataset"
```

默认模式遇到某个 Segment 失败时只记录该 Segment，继续运行当前数据集的后续 Segment，当前数据集结束后再继续下一个数据集。需要人工调试时使用首次失败立即退出模式：

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

`--fail-fast` 不等待失败阈值，第一次数据集或算法运行失败就保存当前事实并返回非零退出码。算法 Segment 运行过程中，用户主动按下 `Ctrl+C` 时，两种模式都会终止当前算法进程并停止本次测试；系统保留已经写入的日志、回执和 `checkpoint.yaml`。

### 恢复暂停或中断的测试

如果运行过程中数据集根目录或必需输入突然消失（例如外接数据盘掉线），系统不会把它记为算法失败，也不会继续把后续数据集批量记为失败。当前测试会显示 `PAUSED`，保存当前日志、回执和 `checkpoint.yaml`，并将当前数据集及后续数据集保留为未运行。

恢复需要手动执行，具体步骤如下：

1. 从原命令最后输出的 `result:` 找到本次测试目录，例如
   `result/orbslam3_mono_sf/test-016`。
2. 如果是数据盘掉线，先重新挂载数据盘，并确认数据集仍位于原来的绝对路径。
3. 确认测试目录内存在 `checkpoint.yaml`。
4. 使用与原运行完全相同的参数重新执行 `benchmark run`，并在末尾增加
   `--resume TEST_DIR`。

默认模式的完整示例：

```bash
cd /path/to/benchmark

RESULT="$PWD/result/ALGORITHM_ID/TEST_ID"

test -f "$RESULT/checkpoint.yaml"

benchmark run \
  --algorithm-config /path/to/algorithm.yaml \
  --dataset-config /path/to/dataset.yaml \
  --resume "$RESULT"
```

例如需要恢复 `orbslam3_mono_sf/test-016`：

```bash
RESULT="$PWD/result/orbslam3_mono_sf/test-016"

benchmark run \
  --algorithm-config configs/orbslam3_mono_sf.local.yaml \
  --dataset-config configs/dataset_01_normal_rk3399.local.yaml \
  --failure-threshold 1 \
  --timeout-seconds 1800 \
  --rpe-delta 100 \
  --rpe-unit m \
  --resume "$RESULT"
```

如果原运行使用了 `--fail-fast`，恢复时也必须保留该选项：

```bash
benchmark run \
  --algorithm-config configs/orbslam3_mono_sf.local.yaml \
  --dataset-config configs/dataset_01_normal_rk3399.local.yaml \
  --fail-fast \
  --resume "$RESULT"
```

恢复命令必须继续提供原运行使用的全部 `--dataset-config` 和
`--dataset-path`，并保持 `--fail-fast`、`--failure-threshold`、
`--timeout-seconds`、`--rpe-delta`、`--rpe-unit` 等参数不变。算法配置、
数据集配置、Git 状态、构建脚本、构建产物、算法契约以及数据集和 Segment
信息也必须保持不变，否则系统会拒绝恢复。

恢复会继续使用原来的 `TEST_ID`，不会创建新的测试目录。系统保留之前已经
完成的数据集，清理当前未完成数据集的数字 Segment 目录，并从当前数据集
的第一个有效 Segment 重新运行。

普通新运行会在当前算法结果目录下自动分配下一个 `test_id`。完整 commit 保存在回执和冻结配置中，不作为目录层级。编译产物保留在算法仓库中，默认结果结构为：

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
        ├── run_summary.xlsx              # 仅绑定评估工作流且已进入汇总流程时生成
        ├── run_summary_20260803_163501.xlsx  # evaluate 命令生成的带时间戳报告
        └── dataset/
            ├── 0/
            │   ├── receipt.yaml
            │   ├── stdout.log
            │   ├── stderr.log
            │   ├── FIXED_OUTPUT
            │   ├── CALIBRATION_FILE         # 仅评估工作流需要
            │   ├── home_point.txt           # 仅真实 sf_vloc 算法输出
            │   └── evaluation/
            │       ├── sf_vo/               # 按工作流和轨迹文件分目录
            │       │   ├── metrics.json     # 评估成功时保证存在且有效
            │       │   ├── receipt.yaml     # 执行评估后生成
            │       │   └── voeval.log       # 执行评估后生成
            │       └── sf_vo_vo_other.txt/  # 多个轨迹文件评估时的独立目录
            │           ├── metrics.json
            │           ├── receipt.yaml
            │           └── voeval.log
            ├── 1/
            └── ...
```

运行成功并完成输出复制后，系统立即以当前数字 Segment 目录作为
`log_dir` 调用 PATH 中的 `voeval`。每次执行评估都会在 `evaluation/` 中
保存 `receipt.yaml` 和 `voeval.log`；只有评估成功并产生合法指标时才保存
有效的 `metrics.json`，评估失败时不保证该文件存在或有效。评估失败不会增加
算法失败次数，失败原因会写入评估回执和
`run_summary.xlsx`，随后继续处理下一个 Segment。未绑定评估工作流的算法
不会调用 `voeval`，也不会生成 `run_summary.xlsx`。

`run_summary.xlsx` 只有一个 `Summary` 工作表，第一列为运行编号，第二列
为可点击的数字 Segment 结果路径，第三列为可点击的原始数据集路径。
SF VO 按本次运行选择的 RPE 间隔汇总平移误差的 RMSE、Mean、Median、
Max、Min、Count，以及断点切分后的 Segment 数量；未设置时默认使用
`100 m`。SF VLOC 汇总轨迹长度以及水平位置、垂直位置和欧拉角的平均与
最大误差。VLOC 的 `mean_error_pos_xy` 大于 20m 时标黄，大于 50m 时
标红。算法运行失败、评估失败和未运行 Segment 也保留一行，并显示运行
状态、评估状态和失败原因。

已经存在的 test 目录可以单独重新生成 Excel：

```bash
benchmark report \
  --test-dir /path/to/result/ALGORITHM_ID/TEST_ID
```

该命令从 test 目录中读取冻结配置、Segment 回执和已经保存的
`metrics.json`，自动判断 SF VO 或 SF VLOC 表格格式，并重新生成或覆盖
`run_summary.xlsx`。它不会重新编译算法、运行数据集或调用 voeval；缺失或
失败的运行与评估结果仍按保存的事实写入表格。

### 重新评估已有测试结果

如果需要对已完成的测试重新执行 voeval 评估（例如调整 RPE 参数或更新评估逻辑），可以使用 `evaluate` 命令：

```bash
benchmark evaluate --test-dir /path/to/result/ALGORITHM_ID/TEST_ID
```

该命令会读取测试目录中的冻结配置和 Segment 运行结果，对所有成功的 Segment 重新执行 voeval 评估，并生成带时间戳的报告文件（例如 `run_summary_20260803_163501.xlsx`）。原有的 `run_summary.xlsx` 不会被覆盖。

可以覆盖冻结配置中的 RPE 参数：

```bash
benchmark evaluate \
  --test-dir /path/to/result/ALGORITHM_ID/TEST_ID \
  --rpe-delta 50 \
  --rpe-unit m
```

评估结果保存在各 Segment 目录下的 `evaluation/` 子目录中，格式与首次运行相同。

运行成功后，系统按数据集契约把 voeval 使用的单份外参复制到
当前数字 Segment 目录：RK3399 使用 `calib_raw.yaml`，RK3588 使用
`bottom_calib_raw.yaml`。`sf_vloc` 的 `vloc.txt` 和 `home_point.txt`
均应由真实算法产生，不从数据集复制；当前模拟算法 `algorithm2` 不产生
`vloc.txt`，不能用于验证真实 VLOC 指标。
RK3588 的 `front_calib_raw.yaml` 不进入评估目录。

所有有效 Segment 按本次 run 的稳定顺序从 0 开始预先编号；默认模式下，成功或失败的 Segment 都会创建对应数字目录并保存事实。只有 `--fail-fast`、人工中断或不可恢复错误使后续 Segment 未启动时，异常运行中才可能出现编号空缺。目录层级不再按数据集分组，但每个 `receipt.yaml` 仍记录原始数据集、Segment 和起止时间戳。数据集运行结果集中保存在 `checkpoint.yaml`，不再生成 `dataset_receipt.yaml`。

不安装本项目且不使用虚拟环境时，系统 Python 必须已经能够导入 PyYAML、
Rich 和 openpyxl：

```bash
python3 -c "import yaml, rich, openpyxl; print('runtime dependencies OK')"
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

`tests/fixtures/mock_algorithms/` 中的三个模拟算法会在临时 Git 仓库内编译，不会在源码目录留下构建产物。编译模块测试覆盖成功、非零退出、入口缺失、超时、路径越界、脚本不可执行、HEAD 变化和已跟踪源码变化。运行模块测试覆盖三个数据集类型、带空格路径、固定输入映射、默认继续后续 Segment、`--fail-fast`、数据集选择、缺失输出、超时和检查点恢复。

### 数据集异常识别套件

异常测试数据不保存在仓库中。工具默认在系统临时目录生成 RK3399、RK3588、KITTI、已有实例 YAML 恢复及非法用户配置案例：

```bash
python3 tools/generate_dataset_anomaly_suite.py
python3 tools/verify_dataset_anomaly_suite.py
python3 tools/generate_dataset_anomaly_suite.py --clean
```

测试数据中的视频是最小占位文件，只用于数据集管理校验，不能用于算法运行。生成、验证、清理完成后，源码仓库不会留下测试数据目录。
