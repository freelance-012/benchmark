# 算法手动运行、逐数据集评估与结果汇总工具——需求说明

> 本文按照一次用户手动运行的真实调用链组织需求。
>
> 当前阶段只说明系统需要完成什么工作及各步骤的输入输出关系，不讨论市场方案、总体架构、技术选型或开发计划。

## 1. 需求目标与范围

### 1.1 要解决的问题

用户需要选择一个已经接入的算法，让算法依次运行一个或多个本地数据集。每个数据集运行结束后，应立即完成评估并保存结果；全部数据集处理完成后，再把已经保存的逐数据集结果集中起来，形成一次运行的数据汇总和报告。

本系统需要减少用户手工重复执行算法、调用评估工具、整理结果和制作汇总报告的工作。

### 1.2 使用方式

系统采用本地单用户 CLI 方式，由用户手动发起每一次运行：

1. 用户启动 CLI；
2. 用户选择本次需要运行的算法；
3. 用户提供本地算法仓库路径、构建配置和一个或多个数据集路径；
4. 系统只读记录当前 Git commit 和工作树状态；
5. 系统按照用户提供的构建契约执行构建；
6. 系统依次运行算法；
7. 每个数据集运行完成后立即调用 voeval；
8. 系统保存该数据集的算法输出和评估结果；
9. 全部数据集完成后执行历史结果对比并生成集中汇总。

“手动运行”是指运行的发起、算法选择和数据集路径均由用户决定。用户启动本次 run 后，系统仍应在当前 run 内自动完成逐数据集的算法调用、voeval 评估、结果保存和最终汇总，不要求用户为每个数据集分别执行命令。

### 1.3 明确不做的内容

当前系统不需要：

- 监听 GitHub 或其他代码托管平台；
- 监听 push；
- 自动发现、判断或筛选 Git commit 来决定是否运行；
- 自动切换代码版本；
- 根据代码变化自动修改运行脚本；
- 后台自动创建运行任务；
- 维护算法任务队列；
- 配置算法队列优先级；
- 对运行任务进行自动抢占或调度；
- 根据 Git 信息自动生成代码修改摘要。

系统允许在用户手动发起 run 后只读记录当前本地仓库的 Git commit，用于结果追溯，但该信息不参与自动触发、排队或调度。系统不围绕 commit 建立任务；一次用户手动发起的 run 仍是最基本的运行和保存单位。

### 1.4 运行环境边界

第一版只考虑：

- 一台本地工作站；
- 本机 CPU、GPU 和本地数据集；
- 单个本地用户；
- CLI 操作入口。

第一版不包含 Web 管理平台、账号登录、角色权限、操作审批、多用户协作、多机或分布式运行。

### 1.5 算法之间的边界

系统可以接入 sfvision、vloc 等多个算法，但一次 run 只选择一个算法。

不同算法拥有独立的运行配置、输出约定、voeval 配置、历史 run 和结果目录。历史结果对比只发生在同一个算法内部，不比较不同算法的效果。

## 2. 六项核心功能需求与项目边界

本节是后续设计和编码必须遵守的功能契约。每项功能都明确：

- 输入是什么；
- 需要哪些具体文件或路径；
- 处理过程做到哪一步；
- 输出哪些文件或目录；
- 需要哪些读、写、执行或设备权限；
- 是否依赖 dataset；
- 成功、失败和重试边界。

六项功能的编号表示功能模块，不等于实际执行顺序。为了确保构建产物能够追溯到正确代码，推荐实际调用顺序是：

```text
输出存储：创建 run 目录和 manifest
→ 记录 Git commit：冻结当前代码身份
→ 构建编译：生成本次可执行产物
→ 针对每个 dataset：
     运行代码
     → 立即评估结果
     → 立即保存本数据集检查点
→ 全部 dataset 完成：
     对比结果
     → 输出存储：形成集中汇总和最终报告
```

六项功能的总边界如下，详细文件契约见后续各节：

| 功能 | 核心输入 | 核心处理 | 核心输出 |
| --- | --- | --- | --- |
| 1. 构建编译 | 算法配置、repo/source/build 目录、CMake 或 Bash 构建入口 | 按已声明契约执行 configure/build 并校验产物 | 构建目录中的产物、构建日志、构建 manifest |
| 2. 记录 Git commit | 本地 repo_dir、当前 HEAD、工作树 | 只读解析代码身份和脏状态 | commit.json、status.txt、可选 staged/unstaged patch |
| 3. 运行代码 | 已校验构建产物、运行命令、一个 dataset 路径 | 渲染命令、运行算法、校验原始输出 | 逐数据集原始输出、日志、运行状态 |
| 4. 评估结果 | 当前 dataset、当前算法输出、voeval 配置 | 在每个 dataset 运行成功后立即调用 voeval | 独立评估快照、metrics.json、可选完整 JSON |
| 5. 对比结果 | 当前与参考 run 的兼容评估快照 | 按 dataset 和指标对齐并计算变化百分比 | comparison.json、不可比项及状态 |
| 6. 输出存储 | 前五项功能的状态和产物 | 隔离落盘、检查点、集中汇总、报告生成 | 完整 run 目录、summary.json、summary.html |

**边界说明：输出存储不是只在最后执行的一步。**

功能 6 是贯穿整个 run 的统一存储能力，负责管理完整的 run 目录：

- run 开始时，创建目录、manifest、配置快照和状态文件；
- 功能 1 至功能 5 执行时，各功能将结果直接写入 run 目录中自己的子目录，功能 6 负责目录约束、原子落盘、索引和检查点；
- 全部 dataset 处理完成后，功能 6 再读取已经保存的结果，生成集中汇总和最终报告。

因此，前五项的输出最终都属于功能 6 管理的完整 run 目录，但不存在“先输出到另一处，最后再统一搬入”的额外步骤。外部 build_dir 是唯一常见例外：构建可以在外部目录执行，但声明的构建产物必须按照 copy 或 reference 策略写入 build manifest 并纳入本次 run 管理。

### 2.1 六项功能共用的文件契约

#### 2.1.1 算法配置文件

每个算法需要一份明确的配置文件。目标标准路径为：

```text
configs/algorithms/<algorithm-id>.yaml
```

当前仓库已有 pipeline.yaml 和 configs/pipeline_sf_slam.yaml，但尚未包含完整的构建与 Git 记录契约。后续实现可以迁移或扩展现有 YAML；本节定义的是最终需要达到的字段边界。

配置文件至少需要表达：

```yaml
algorithm:
  id: sfvision
  name: SFVision
  repo_dir: /absolute/path/to/repository

git:
  ref: HEAD
  allow_dirty: false

process:
  cancel_grace_seconds: 10

build:
  method: cmake            # cmake | bash | none
  source_dir: /absolute/path/to/repository
  build_dir: /absolute/path/to/build
  working_dir: /absolute/path/to/repository
  cmake_file: CMakeLists.txt
  toolchain_file: null
  build_script: null
  env_script: null
  env: {}
  configure_args: []
  build_args: []
  target: null
  clean_before_build: false
  timeout_seconds: 1800
  artifact_storage: copy   # copy | reference
  expected_artifacts:
    - id: main_executable
      kind: file           # file | directory
      path: /absolute/path/to/build/bin/algorithm
      entrypoint: null     # directory 型可运行 artifact 使用相对目录的入口

run:
  mode: executable         # executable | script
  working_dir: "{attempt_dir}"
  artifact_id: main_executable
  command_template: "{executable} --data_dir={dataset_path} --output_dir={output_dir}"
  args_template: null
  env_script_artifact_id: null
  env: {}
  required_dataset_files:
    - imu.txt
  expected_output_files:
    - vo.txt
  output:
    mode: managed          # managed | external_exact
    external_path_template: null
    import_mode: copy
  time_range:
    mode: none             # none | file_first_last
    source_file: null
    column: 0
    delimiter: whitespace
    unit: s
  timeout_seconds: 3600
  max_retries: 1
  max_failure_ratio: 0.2  # 必填，范围 0.0～1.0；此处为示例值

evaluation:
  mode: sf_vo             # sf_vo | sf_vloc
  invocation: python_api  # python_api | cli
  python_executable: /absolute/path/to/python
  expected_voeval_version: null
  timeout_seconds: 600
  hardware: rk3399        # rk3399 | rk3588
  vo_filename: vo.txt
  staging_mode: symlink   # symlink | copy
  input_mapping:
    imu_file: "{dataset_path}/imu.txt"
    trajectory_file: "{raw_output_dir}/vo.txt"
    calibration_file: "{dataset_path}/calib_raw.yaml"
  delta_value: 100
  delta_unit: m
  save_full_report: false

storage:
  results_root: /absolute/path/to/results
  min_free_bytes: 1073741824
```

以上字段及语义构成本需求阶段的配置基线。详细设计可以增加可选字段，但不能删除必需信息或在没有迁移说明时改变字段含义。

run.command_template 中的 {output_dir} 指当前 attempt 的 output/；evaluation.input_mapping 中的 {raw_output_dir} 指成功 attempt 提升后的稳定 raw-output/。两者不能混用。

evaluation.input_mapping 只允许 {dataset_path}、{dataset_id}、{run_id} 和 {raw_output_dir} 四个占位符；映射结果必须是已存在的普通文件并记录 SHA-256，不能执行通配符或“取最新文件”逻辑。

路径解析规则固定如下：

- repo_dir、source_dir、build_dir、python_executable 和 results_root 在 resolved.json 中必须是绝对路径；
- build.working_dir 的相对路径以 repo_dir 为基准，resolved.json 中必须保存为绝对路径；
- cmake_file 和 toolchain_file 的相对路径以 source_dir 为基准；
- build_script 和 build.env_script 的相对路径以 repo_dir 为基准；
- expected_artifacts.path 的相对路径以 build_dir 为基准；
- 所有路径在执行前规范化并记录最终绝对路径，不能依赖启动 CLI 时的当前目录。

运行模板允许的占位符只有：{dataset_path}、{dataset_id}、{run_id}、{attempt_no}、{attempt_dir}、{output_dir}、{external_output_dir}、{artifact_path}、{start_ts} 和 {end_ts}。{executable} 是 {artifact_path} 的兼容别名。出现未声明占位符时在启动前报 configuration_error。

time_range.source_file 的相对路径以当前 dataset_path 为基准，也可以使用 {dataset_path} 绝对模板，但规范化后不得逃逸 dataset 根目录。time_range.mode=none 时 start_ts/end_ts 不可在模板中使用；file_first_last 时，Pipeline 从 source_file 指定列读取第一条和最后一条有效记录，并按 delimiter 和 unit 解析。文件不存在、列越界或无法解析时记为 dataset_input_invalid，不启动算法。

artifact.kind=file 时，resolved_path 就是冻结后的该文件；kind=directory 时，manifest 必须保存目录文件清单，run.artifact_id 指向可运行目录时 entrypoint 必填，最终 artifact_path 为 resolved_path/entrypoint。entrypoint 必须是目录内相对路径，不能包含 .. 或逃逸 artifact 根目录。

run.output.mode=managed 时算法直接写 attempt 的 {output_dir}，此时 {external_output_dir} 不可用。external_exact 时 external_path_template 必须包含 {run_id} 和 {attempt_no}，渲染后的路径通过 {external_output_dir} 传给 command_template/args_template，目标在运行前必须不存在，运行后按 import_mode=copy 导入 attempt/output/；Pipeline 只允许清理本次 run/attempt 对应的外部路径，禁止扫描“最新目录”或清理共享父目录。

expected_output_files 全部相对当前 attempt/output/ 解析，不允许绝对路径或 ..。外部输出也必须先导入 attempt/output/，再使用同一规则校验。

#### 2.1.2 公共结果根目录

一次 run 的统一输出根目录为：

```text
<results-root>/<algorithm-id>/runs/<run-id>/
```

任何变更型操作都可以在 results_root 根创建临时 `.pipeline.lock`。正常执行一次新 run 时，除该锁外，六项功能只能写入当前 run-id 目录以及用户明确配置的外部 build_dir，不能修改历史 run、算法源码或 dataset。算法首次接入或用户显式更新接入配置时，可以创建或原子更新算法根目录的 `algorithm.json`；没有 current run 的临时对比可以写算法根 `comparisons/<comparison-set-id>/`；用户显式发起重评、重新对比或重新生成报告时，可以向历史 run 追加新的带 id 目录，并原子更新 `index.json` 及 `status.json` 的 current 指针，但不得改写旧产物或 initial_outcome；用户显式设置 baseline/reference 时，可以原子更新算法根目录的 `baseline.json` 或 `references.json`。这些写入都不得修改被引用的历史结果内容。

run 初始化时必须把用户提供的算法配置和本次覆盖参数解析成不可变快照：

```text
<run-root>/config/
├── algorithm.yaml          # 用户输入配置的原始快照
└── resolved.json           # 合并 CLI 覆盖项后的实际配置
```

后续构建、运行、评估和报告只能读取 resolved.json，不能在 run 中途悄悄重新加载已经变化的外部 YAML。

#### 2.1.3 标识与状态文件约定

- algorithm-id 只允许匹配 [a-z0-9][a-z0-9_-]{0,63}，不得包含路径分隔符或 ..；
- operation-id、run-id、evaluation-id、comparison-set-id 和 report-id 使用 UTC 时间戳加 8 位随机十六进制数，格式为 YYYYMMDDTHHMMSSffffffZ-xxxxxxxx，并在对应父目录内唯一；
- dataset_version 未提供时，resolved 值固定为空字符串，不使用 null 或自动读取目录时间；
- dataset 路径先解析为 realpath；dataset basename 先做 Unicode NFC 归一化，只保留 ASCII 字母、数字、点、下划线和连字符，其他字符替换为下划线，连续下划线合并，去掉首尾的点/下划线/连字符，空结果使用 dataset，最后截取 48 个字符；
- dataset-id 固定为“上述清洗 basename-SHA256(realpath + 换行 + resolved dataset_version) 的前 12 位”；
- 同一 run 中相同 realpath 和 dataset_version 只允许出现一次，重复输入在启动前拒绝；
- 每次重评必须生成新的 evaluation-id；
- artifact、算法配置快照和关键评估输入使用 SHA-256 记录校验值；
- 所有 status.json 至少包含 schema_version、state、started_at、finished_at、error_code、message 和 artifacts；
- run 根 status.json 还必须包含不可变的 initial_outcome、当前 current_outcome 和 current_report_id；
- 每份 `reports/<report-id>/summary.json` 保存生成当时独立计算的 outcome 和所采用的 evaluation-id，旧报告不可变；
- 重评后重新生成报告时，可以根据新评估快照重算 current_outcome 并原子更新 run 根 status.json 的 current_outcome/current_report_id，但不得改写 initial_outcome 或旧报告；
- manifest.json、input.json、metadata.json、metrics.json、index.json、comparison.json、summary.json 以及可导入报告都必须包含 schema_version；
- 读取历史或导入 JSON 时只接受当前明确支持的 schema 版本，不支持时必须拒绝并说明所需迁移，不能按相似字段猜测；
- JSON 状态先写临时文件，再使用原子重命名替换目标文件，避免中断后留下半个 JSON。

`<results-root>/.pipeline.lock` 是使用“仅当文件不存在时创建”语义获得的 JSON 锁文件，固定包含：schema_version、operation_id、operation_type、host、pid、started_at、algorithm_id、run_id 和 target_ids。operation_type 只允许 start_run、resume_run、reevaluate、compare、generate_report、update_algorithm、set_baseline、update_references；没有 current run 的操作把 run_id 写为 null，并在 target_ids 中记录 comparison-set-id、report-id 或被更新引用。锁文件不能假设所有操作都有 run-id。

#### 2.1.4 构建文件的输入输出性质

| 文件或目录 | 性质 | 说明 |
| --- | --- | --- |
| `<source-dir>/<cmake-file>` | 构建输入 | CMake 工程入口，不是 Pipeline 生成物；cmake-file 相对 source_dir 解析 |
| `<source-dir>/**/*.cmake` | 构建输入 | 模块或工具链文件，只读 |
| `<repo-dir>/build.sh` 或用户指定脚本 | 构建输入 | Bash 构建入口，需要读取和执行权限 |
| `<build-dir>/` | 构建输出 | CMake 缓存、中间文件、目标文件和最终产物 |
| 可执行文件、动态库、静态库 | 构建输出 | 后续“运行代码”功能的输入 |
| `<run-root>/build/*.log` | Pipeline 输出 | 配置和编译日志 |
| `<run-root>/build/manifest.json` | Pipeline 输出 | 构建方式、命令、状态和产物路径清单 |

### 2.2 功能 1：构建编译

#### 输入

必需输入：

- 算法配置文件路径；
- repo_dir：本地算法仓库根目录；
- source_dir：需要构建的源码目录，必须等于 repo_dir 或位于 repo_dir 内；
- build_dir：允许写入的独立构建目录；
- build.method：cmake、bash 或 none；
- expected_artifacts：构建成功后必须存在的 artifact id、可执行文件、脚本、库或运行时代码目录；
- artifact_storage：copy 或 reference；
- 构建工作目录、超时、环境变量和可选环境初始化脚本。

如果某个实际参与编译的源码目录不在 repo_dir 内，不能把它隐式当作依赖跳过记录；用户必须把它作为独立代码源接入并提供版本或文件校验契约。第一版不支持未登记的外部源码树。

当 build.method 为 cmake 时，还需要：

- `<source-dir>/<cmake_file>`；
- 可选的 .cmake 模块或 toolchain_file；
- CMake configure 参数；
- CMake build 参数；
- 可选的 target 名称。

当 build.method 为 bash 时，还需要：

- build_script 的具体路径，例如 `<repo-dir>/build.sh`；
- 脚本工作目录；
- 脚本参数，统一使用 build_args 按顺序传给 build_script；
- 可选的环境初始化脚本路径。

当 build.method 为 none 时：

- 用户必须直接提供已存在、可执行的 artifact；
- Pipeline 只校验并冻结产物，不执行编译；
- manifest 必须把 provenance 标为 external_prebuilt，不能声称该 artifact 是由本次 Git commit 构建得到。

#### 处理过程

- **步骤 1：** 校验算法配置、source_dir、build_dir 和构建入口存在；
- **步骤 2：** 校验 source_dir 位于 repo_dir 内，并校验 build_dir 不覆盖源码目录，默认采用 out-of-source build；
- **步骤 3：** 记录实际构建命令、工作目录和环境摘要；
- **步骤 4：** 在独立子进程中应用 env_script 和 env，不修改用户登录 shell；
- **步骤 5：** 创建或使用用户指定的 build_dir；
- **步骤 6：** cmake 模式先执行 configure，再执行 build；
- **步骤 7：** bash 模式执行指定 build_script；
- **步骤 8：** 保存标准输出、错误输出、退出码和耗时；
- **步骤 9：** 校验 expected_artifacts 全部存在且类型正确；目录型 artifact 必须递归生成文件清单；
- **步骤 10：** 记录文件产物的 SHA-256，目录产物中每个文件的相对路径、大小和 SHA-256；
- **步骤 11：** artifact_storage=copy 时，将全部声明产物复制到本次 run 的 build/artifacts/；reference 时只记录外部绝对路径；
- **步骤 12：** 构建结束后再次确认 HEAD 和工作树与构建前快照完全一致。

默认执行增量构建，不清空外部 build_dir。只有 clean_before_build=true 且 Pipeline 已确认 build_dir 不是 repo_dir、source_dir、dataset、results_root 或历史 run 时，才允许清理该 build_dir。

build.timeout_seconds 在 cmake 模式下分别作用于 configure 和 build 两个子进程，每一步都有完整时限；在 bash 模式下作用于整次 build_script；none 模式不启动构建子进程。日志必须分别记录具体是哪一步超时。

#### 输出

至少输出：

```text
<run-root>/build/
├── status.json
├── configure-command.json # 仅 cmake
├── configure.log          # 仅 cmake
├── build-command.json     # cmake 或 bash
├── build.log              # cmake 或 bash；none 时不生成
├── environment.json
├── artifacts/             # artifact_storage=copy 时
│   └── <artifact-id>/
└── manifest.json
```

manifest.json 至少包含：

- 构建方式；
- source_dir；
- build_dir；
- 实际命令；
- cmake_file、toolchain_file、build_script、env_script 等构建入口的绝对路径和 SHA-256；
- 开始、结束时间；
- 退出码；
- 成功或失败状态；
- expected_artifacts 的 id、kind、输入绝对路径、entrypoint、冻结后的 resolved_path；
- 文件或目录清单的产物校验值；
- 与本次 Git 记录的关联标识。

条件不适用的 command/log 文件不要求创建，但 manifest.json 必须明确记录对应步骤为 not_applicable，不能让“文件缺失”同时表示跳过和执行失败。

artifact_storage 默认使用 copy。此时后续运行只允许读取 build/artifacts/ 中的冻结产物。使用 reference 时，Pipeline 可以不复制外部 build_dir 的产物，但必须在每个 dataset 启动前以及中断恢复时重新校验 SHA-256；校验不一致时标记 artifact_changed，停止本次 run，不得自动使用新文件或把它计为算法失败。

expected_artifacts 必须覆盖运行期间会加载的全部代码、脚本、动态库和关键运行配置。script 模式的工作目录、PYTHONPATH 或其他模块搜索路径不得指回 repo_dir 的实时源码；需要使用的 Python 包或脚本目录必须作为目录型 artifact 冻结并从 manifest 路径加载。

#### 权限

- repo_dir 和 source_dir：读取、目录遍历权限；
- `<source-dir>/<cmake_file>`、`.cmake` 和构建配置：读取权限；
- build_script 和构建工具：执行权限；
- build_dir：创建目录、读写和删除构建中间文件的权限；
- run-root/build：读写权限；
- 编译器、CMake、Ninja/Make 等工具：执行权限；
- 默认不需要 GitHub 网络权限；
- 默认不允许 sudo 或安装系统级依赖，确有需要时必须由用户在运行前单独准备或明确授权。

#### Dataset 需求

构建编译不读取正式 dataset。

如果算法接入需要构建后 smoke test，smoke test 属于运行功能，应由用户另外提供轻量测试 dataset 路径，不能把正式 dataset 隐式作为编译输入。

#### 成功与失败边界

成功条件：

- 构建命令退出码为 0；
- expected_artifacts 全部存在；
- copy 模式下声明产物已完整复制并通过二次 SHA-256 校验；
- reference 模式下外部产物与 manifest 校验值一致；
- manifest.json 和构建日志成功落盘。

以下任一情况判定构建失败：

- 配置或构建入口缺失；
- build_dir 不可写；
- CMake configure 失败；
- build_script 或编译命令退出非 0；
- 超时；
- 预期产物缺失；
- 构建期间 HEAD、已跟踪文件、未跟踪文件或 submodule 相对构建前快照发生变化；
- 产物复制或校验失败；
- 构建日志或 manifest 无法保存。

构建失败后不进入正式运行和评估，但仍进入输出存储功能，形成本次失败记录。

### 2.3 功能 2：记录 Git commit

该功能只记录本次手动 run 使用的本地代码身份，不负责监听、筛选、切换或创建 commit。

#### 输入

- repo_dir；
- `<repo-dir>/.git/`；
- 可选 git.ref，可由 CLI 覆盖，默认使用 HEAD；
- 可选 allow_dirty 设置；
- 当前 run-id。

第一版不自动 checkout。用户提供 git.ref 时，系统只验证它是否与当前工作目录的 HEAD 一致；不一致时提示用户手动切换后重新运行。

#### 处理过程

- **步骤 1：** 校验 repo_dir 是有效本地 Git 仓库；
- **步骤 2：** 将 HEAD 解析为完整 commit hash；
- **步骤 3：** 读取短 hash、commit message、作者、提交时间和当前分支或 detached HEAD 状态；
- **步骤 4：** 读取工作树是否存在未提交修改；
- **步骤 5：** 读取暂存区和未暂存文件状态；
- **步骤 6：** 有 submodule 时记录 submodule commit；
- **步骤 7：** 工作树不干净时分别保存暂存区和未暂存的已跟踪文件差异，并记录未跟踪文件路径和 SHA-256；
- **步骤 8：** 根据 allow_dirty 决定拒绝或继续；
- **步骤 9：** 构建结束后再次读取 HEAD 和工作树状态，确认 Pipeline 没有改变源码身份。

#### 输出

至少输出：

```text
<run-root>/git/
├── commit.json
├── status.txt
├── submodules.json        # 存在 submodule 时
├── staged.patch           # 暂存区有修改且允许继续时
├── unstaged.patch         # 已跟踪文件有未暂存修改时
└── untracked.json         # 存在未跟踪文件时
```

commit.json 至少包含：

- repo_dir；
- full_hash；
- short_hash；
- branch 或 detached 状态；
- commit message；
- author name；
- author email；
- author time；
- commit time；
- dirty 标记；
- 记录时间；
- 构建前后 Git 身份校验结果。

staged.patch 和 unstaged.patch 分别保存已跟踪文件的暂存区与工作区差异；untracked.json 保存未跟踪文件的相对路径、大小和 SHA-256，默认不复制未跟踪文件内容。allow_dirty=true 只能证明本次构建输入，不保证仅凭 commit 可以完全复现未跟踪文件内容，报告必须明确标为 dirty build。

#### 权限

- repo_dir 和 .git：只读及目录遍历权限；
- Git 可执行程序：执行权限；
- run-root/git：读写权限；
- 不需要 GitHub token；
- 不需要远端网络权限；
- 不允许执行 commit、push、pull、merge、rebase、reset 或自动 checkout；
- 不允许修改 .git 或算法源码。

#### Dataset 需求

无。

#### 成功与失败边界

成功条件：

- HEAD 能解析为完整 hash；
- commit.json 成功落盘；
- 初始工作树干净，或初始 dirty 状态已经被完整记录且 allow_dirty=true；
- 构建完成后的 HEAD、工作树、未跟踪文件和 submodule 状态与初始 Git 快照完全一致。

以下情况记录失败：

- repo_dir 不是 Git 仓库；
- HEAD 无法解析；
- 用户指定 ref 与当前 HEAD 不一致；
- 工作树不干净且用户不允许继续；
- Git 快照完成后源码状态发生任何变化；
- Git 元数据无法写入结果目录。

Git 记录失败时，默认不继续构建和运行，避免产生无法追溯的结果。

### 2.4 功能 3：运行代码

#### 输入

- 算法配置文件；
- 成功的 `<run-root>/build/manifest.json`，或者 method=none 时已校验的 artifact；
- run.mode：executable 或 script；
- executable 模式下，build manifest 中的 artifact_id 和 command_template；
- script 模式下，build manifest 中代表已冻结脚本及其运行时代码的 artifact_id 和 args_template；
- 当前一个 dataset 的绝对路径；
- 当前 dataset 的可选版本标识；
- 本数据集专属 output_dir；
- 运行参数、超时、最大重试次数和本次 run 的 max_failure_ratio；
- 运行所需环境变量，以及可选的已冻结 env_script_artifact_id。

两种运行模式互斥：executable 模式使用 artifact_id + command_template；script 模式使用 artifact_id + args_template。两种模式的实际入口都必须从 build manifest 解析，禁止在 dataset 循环中直接执行 repo_dir 里的实时文件。运行入口、工作目录和环境必须来自本次 resolved.json。

#### 处理过程

- **步骤 1：** 校验 build manifest 与 artifact 的 SHA-256；
- **步骤 2：** 校验 dataset 路径及该算法声明的必需文件；
- **步骤 3：** 将本数据集最终标准输出目录固定为 `<run-root>/datasets/<dataset-id>/run/raw-output/`；
- **步骤 4：** 为每次实际尝试创建隔离的 `attempts/<attempt-no>/output/`，并在该次命令中把 `{output_dir}` 解析为这个 `attempt/output/`；
- **步骤 5：** 渲染并保存最终运行命令；
- **步骤 6：** 执行 manifest 中冻结的算法程序或脚本；
- **步骤 7：** 应用超时和最大重试次数；max_retries 表示首次执行之外允许的重试次数，总尝试数为 1 + max_retries；
- **步骤 8：** 每次尝试分别捕获 stdout、stderr、退出码和耗时；
- **步骤 9：** 只检查当前 attempt/output/ 中的约定输出，不复用前一次残留文件；
- **步骤 10：** 某次尝试成功后，把其约定输出原子复制或重命名为标准 raw-output；
- **步骤 11：** 写入运行状态和元数据；
- **步骤 12：** 算法成功后立即把 data_dir 和 log_dir 交给评估功能，不先运行下一个 dataset。

新接入算法必须直接接受每次尝试的 output_dir。旧算法如果只能写外部目录，必须使用 run.output.mode=external_exact 和 external_path_template，并在返回后复制到对应 attempt/output/，成功后再提升为 raw-output；禁止通过“寻找最新数字目录”或扫描最近修改文件来猜测本次输出。

#### 输出

每个 dataset 至少输出：

```text
<run-root>/datasets/<dataset-id>/run/
├── command.json
├── status.json
├── metadata.json
├── attempts/
│   └── <attempt-no>/
│       ├── command.json
│       ├── stdout.log
│       ├── stderr.log
│       ├── status.json
│       └── output/
└── raw-output/
    └── <算法约定的原始输出文件>
```

metadata.json 至少记录：

- dataset 绝对路径和版本；
- 实际运行命令；
- 实际 entrypoint artifact-id 和冻结路径；
- build artifact 校验值；
- 开始、结束时间；
- 退出码；
- 重试次数；
- output_dir；
- 算法运行状态。

#### 权限

- build manifest 中冻结的 entrypoint artifact 和依赖程序：读取和执行权限；
- dataset 目录及必需文件：只读和目录遍历权限；
- `run-root/datasets/<dataset-id>/run`：读写权限；
- 临时目录：读写权限；
- 算法所需动态库：读取权限；
- 需要 GPU 时，运行用户必须具有相应 GPU 设备和驱动访问权限；
- 不允许修改 dataset、真值、标定、算法源码或其他历史 run。

#### Dataset 需求

每个算法必须在配置中声明：

- dataset_path 如何传给算法；
- 判断 dataset 有效所需的文件列表；
- 算法运行需要读取的原始数据、标定和其他配置；
- 是否需要起止时间；
- 起止时间从哪个文件读取；
- 输出目录如何传给算法。

对当前 SF VO/VLOC 数据，至少需要能够从 dataset 路径读取 imu.txt。算法自身如果还需要图像、视频、传感器文件或配置，必须在对应算法配置中完整声明，Pipeline 不负责猜测。

#### 成功与失败边界

算法进程正常结束、退出码为 0，并且所有 required output 存在且非空，判定运行成功。

以下情况判定算法运行失败：

- 启动失败；
- 超时；
- 全部 1 + max_retries 次尝试均未成功；
- 退出码非 0；
- 当前 attempt 的必需算法输出缺失或为空。

以下情况不属于算法运行失败，也不计入算法失败率：

- run 创建后 dataset 路径变得不可读/不存在，或必需输入缺失：记为 dataset_input_invalid，不启动算法；
- artifact 缺失或 SHA-256 变化：记为 artifact_changed，停止本次 run；
- 运行入口或配置缺失：记为 configuration_error，停止本次 run；
- max_failure_ratio 缺失或不在 0.0～1.0：记为 configuration_error，在启动算法前停止；
- 状态或输出无法落盘：记为 storage_failed，停止本次 run。

单个 dataset 运行失败后，保存记录并继续下一个 dataset，不调用 voeval。

### 2.5 功能 4：评估结果

该功能在每个 dataset 算法运行成功后立即执行，属于逐数据集循环，不能延迟到全部 dataset 运行结束后再批量执行。

#### 输入

共用输入：

- 当前 dataset 的 data_dir；
- 当前算法输出的 log_dir；
- voeval 模式：sf_vo 或 sf_vloc；
- invocation：python_api 或 cli；
- python_executable 和期望 voeval 版本；
- evaluation.timeout_seconds；
- hardware：rk3399 或 rk3588；
- voeval 版本；
- delta_value；
- delta_unit：m 或 f；
- input_mapping：dataset 文件和算法输出到 voeval 固定文件名的映射；
- staging_mode：symlink 或 copy；
- save_full_report：是否保存完整 voeval JSON；
- 当前 dataset 的评估输出目录。

sf_vo 的固定输入文件：

```text
<data-dir>/
└── imu.txt

<log-dir>/
├── <vo-filename>           # 默认 vo.txt，可配置
└── calib_raw.yaml          # hardware=rk3399
    或 bottom_calib_raw.yaml # hardware=rk3588
```

sf_vloc 的固定输入文件：

```text
<data-dir>/
└── imu.txt

<log-dir>/
├── vloc.txt
├── home_point.txt
└── calib_raw.yaml          # hardware=rk3399
    或 bottom_calib_raw.yaml # hardware=rk3588
```

注意：voeval CLI 中的 --dataset 实际选择 rk3399/rk3588 标定文件，不是 Pipeline 的 dataset 路径。

算法配置必须明确每个固定文件来自哪里。sf_vo 至少映射 imu_file、trajectory_file 和 calibration_file；sf_vloc 至少映射 imu_file、vloc_file、home_point_file 和 calibration_file。Pipeline 不根据目录内容猜测来源。

sf_vo staging 后的轨迹文件名必须等于配置的 vo_filename，并把同一名称传给 voeval；sf_vloc 始终使用 vloc.txt。不能一边允许自定义 vo_filename，一边把输入视图硬编码为 vo.txt。

为了适配 voeval 固定的 data_dir/log_dir 布局，Pipeline 在 evaluation-id 下创建只读输入视图：

```text
<evaluation-dir>/input/
├── data/
│   └── imu.txt
└── log/
    ├── <vo_filename> 或 vloc.txt
    ├── home_point.txt             # 仅 sf_vloc
    └── calib_raw.yaml
        或 bottom_calib_raw.yaml
```

这些文件由 input_mapping 指向 dataset 或 raw-output 中的真实来源，默认使用符号链接；需要完全独立保存时可以配置为复制。copy 只复制 voeval 本次要求的映射文件，不复制整个 dataset。无论哪种模式，都不得修改源 dataset 或算法原始输出。

#### 处理过程

- **步骤 1：** 解析 input_mapping 并校验所有源文件；
- **步骤 2：** 创建 data/ 和 log/ 评估输入视图并保存 input-map.json；
- **步骤 3：** 校验 data_dir、log_dir 和 voeval 固定文件名；
- **步骤 4：** 校验 python_executable 可执行、voeval 可导入，并记录实际版本；配置了 expected_voeval_version 时必须一致，未配置时以本 run 第一次评估检测到的版本作为冻结版本；
- **步骤 5：** 根据模式和 invocation 构造 voeval 调用；
- **步骤 6：** 执行单数据集输入解析、坐标与时间处理、轨迹对齐和指标计算；
- **步骤 7：** Pipeline 读取结构化结果，不解析终端文本；
- **步骤 8：** 保存 voeval 版本、配置、输入路径和状态；
- **步骤 9：** 成功或失败结果落盘后，才允许进入下一个 dataset。

save_full_report=false 时 Pipeline 只要求稳定 metrics.json，可以使用 CLI -o 的数据进行归一化。当前契约中 save_full_report=true 只允许 invocation=python_api，并保存 full-report.json；与 invocation=cli 组合时在启动前报 configuration_error，不自动切换调用方式。CLI 精简 JSON 不能冒充完整报告。水平/垂直 ATE、覆盖率、断点、RPE 旋转、局部尺度或逐帧字段依赖完整报告。

无论 invocation=cli 还是 python_api，voeval 都必须在使用 python_executable 启动的独立 worker 子进程中执行；python_api 表示 worker 内部调用 voeval API，不表示在 Pipeline 主进程内直接运行。这样才能隔离环境、捕获日志并执行超时或取消。

#### 输出

每次评估至少输出：

```text
<run-root>/datasets/<dataset-id>/evaluations/
├── index.json
└── <evaluation-id>/
    ├── input-map.json
    ├── input/
    │   ├── data/
    │   └── log/
    ├── status.json
    ├── config.json
    ├── evaluation.log
    ├── metrics.json
    └── full-report.json    # save_full_report=true 时
```

其中：

- status.json：成功、评估未完成或不可评估；
- metrics.json：Pipeline 对比和汇总所需的稳定指标子集；
- full-report.json：voeval 完整结构化报告；
- evaluation.log：调用日志和异常信息。

index.json 记录全部 evaluation-id、创建时间、评估状态和 initial_evaluation_id。初次 run 的汇总默认使用本轮产生的第一份成功评估；重评产生的新 evaluation-id 只追加到 index，不会自动替换任何旧报告已经绑定的快照。用户要采用新快照时，必须显式选择 evaluation-id，并生成新的 comparison-set-id 或 report-id。

metrics.json 是 Pipeline 汇总和对比使用的稳定接口，第一版至少包含以下结构：

```json
{
  "schema_version": "1.0",
  "algorithm_id": "sfvision",
  "run_id": "<run-id>",
  "dataset_id": "<dataset-id>",
  "evaluation_id": "<evaluation-id>",
  "mode": "sf_vo",
  "evaluator": {
    "name": "voeval",
    "version": "<actual-version>",
    "invocation": "python_api"
  },
  "metrics": {
    "rpe_translation_m": {
      "delta_value": 100,
      "delta_unit": "m",
      "rmse": 0.0,
      "mean": 0.0,
      "median": 0.0,
      "max": 0.0,
      "min": 0.0,
      "count": 0
    },
    "ate_position_m": {
      "rmse": 0.0,
      "mean": 0.0,
      "median": 0.0,
      "max": 0.0,
      "min": 0.0
    },
    "segment_count": 0
  },
  "invalid_metrics": []
}
```

metrics.segment_count 是所有模式共用的主轨迹分段数量。sf_vo 还需要在 metrics.sim3 下保存 segment_count 和 segments 中每段的 segment_id、scale、rotation、translation，其中 scale 无量纲；sf_vloc 还需要在 metrics.vloc_metrics 下保存 trajectory_length_m、mean_error_pos_xy、max_error_pos_xy、mean_error_pos_z、max_error_pos_z、mean_error_euler 和 max_error_euler，其中位置与长度单位为 m，欧拉角误差单位为 deg。扩展指标必须携带稳定键名、单位和定义版本；未知指标可以保留，但在注册比较规则前不得自动参与跨 run 对比。

JSON 中禁止写 NaN 和正负无穷；这些值统一写为 null，并在 invalid_metrics 中记录指标路径和失效原因。

voeval 的详细 HTML 属于单数据集查看能力，可以由用户按需生成，但不作为 Pipeline 集中汇总报告的必需输入，也不嵌入 Pipeline 报告。

#### 权限

- data_dir 和固定输入文件：只读和目录遍历权限；
- log_dir 和算法原始输出：只读和目录遍历权限；
- voeval Python 环境：读取和执行权限；
- evaluation 输出目录：读写权限；
- 使用 symlink 时需要创建和读取符号链接的权限；使用 copy 时需要读取源文件并写入 input/ 的权限；
- 系统临时目录：可选读写权限；
- 只有按需生成 voeval HTML 时才需要 Node.js 执行权限和 HTML 输出目录写权限；
- voeval 不得修改 data_dir、log_dir 或算法原始输出。

#### Dataset 需求

- data_dir 必须存在 imu.txt；
- 真值、标定和算法输出必须与当前 dataset 匹配；
- dataset 路径和版本必须记录；
- 无真值、缺少标定或格式不受支持时，不得伪造指标，应标记为不可评估；
- 历史重评时，对应 dataset 版本必须仍可访问。

#### 成功与失败边界

评估成功条件：

- voeval 正常完成；
- metrics.json 成功生成；
- save_full_report=true 时 full-report.json 成功生成；
- status.json 成功落盘。

解析失败、指标计算失败或 voeval 异常时，记录 evaluation_incomplete，但不得改写为算法运行失败，也不得计入算法失败率。

以下情况必须单独分类：

- dataset 业务上没有真值、标定或受支持口径：evaluation_unavailable；
- input_mapping 配置错误、python_executable 不可用或 voeval 版本不符：configuration_error；
- status.json、metrics.json 或日志无法保存：storage_failed。

configuration_error 和 storage_failed 停止本次 run；evaluation_unavailable 保存原因后继续下一个 dataset。三者都不计入算法失败率。

同一 run 后续 dataset 检测到的 voeval 版本若与冻结版本不同，必须以 configuration_error 停止，不能在一份汇总中混用评估实现。

评估失败后保留原始输出，允许以后只重跑评估。每次重评生成新的 evaluation-id，不覆盖旧结果；这是对历史 run 唯一允许的评估追加操作，现有 evaluation 目录和既有报告保持只读。

### 2.6 功能 5：对比结果

#### 输入

- 自动对比时：当前 run 每个 dataset 的 metrics.json 和当前 run 汇总元数据；
- 临时对比时：用户选择的两份或多份历史 run/report，不要求存在“当前 run”；
- 用户选择的一份或多份上一次成功 run、baseline run、参考 run 或导入报告；
- 参考结果中的 dataset 路径、dataset 版本和评估快照；
- 当前与参考结果的 voeval 版本和评估配置；
- 参与对象的 git/commit.json，仅用于报告展示和追溯，不参与指标计算。

#### 处理过程

- **步骤 1：** 加载本次所有参与对象的 metrics.json；自动对比包含当前 run，临时对比按用户选择顺序加载；
- **步骤 2：** 校验 JSON schema 和评估口径；
- **步骤 3：** 对齐共同可比 dataset；
- **步骤 4：** 对齐同名指标；
- **步骤 5：** 按实际对比对计算左值、右值、绝对差和变化百分比；
- **步骤 6：** 标注变大、变小或不变；
- **步骤 7：** 汇总每项指标的有效、失效和不可比 dataset；
- **步骤 8：** 对同一指标的有效 dataset 变化百分比做等权平均；
- **步骤 9：** 不自动判断改善、退化或回归通过；
- **步骤 10：** 保存不可比原因。

变化百分比公式为：

**（右侧值 - 左侧值）/ 左侧值 × 100%**

自动对比中左侧是参考 run、右侧是当前 run；多 run 临时对比中左侧是较早/排序靠前对象、右侧是后一个对象。comparison.json 必须保存顺序，不能只凭文件名推断。

ATE、RPE 等绝对值不得跨 dataset 求平均。缺失、null、NaN 和正负无穷不参与百分比平均；数值 0 不能统一视为失效，对比分母为 0 的显示规则留到详细设计。

#### 输出

自动生成、锚定当前 run 的对比输出到：

```text
<run-root>/comparisons/
├── index.json
└── <comparison-set-id>/
    ├── sources/            # 包含导入报告时
    ├── status.json
    ├── comparison.json
    ├── incompatible-items.json
    ├── comparison.log
    └── comparison.html
```

任意历史 run 或导入报告之间的临时对比没有 current run，输出到算法级目录：

```text
<results-root>/<algorithm-id>/comparisons/
├── index.json
└── <comparison-set-id>/
    ├── sources/            # 导入报告的不可变副本及 SHA-256
    ├── status.json
    ├── comparison.json
    ├── incompatible-items.json
    ├── comparison.log
    └── comparison.html
```

导入报告必须先校验同一 algorithm-id 和 schema_version，再复制到 sources/；后续临时对比只读取副本，不能依赖可能变化或被删除的外部文件。

comparison.json 至少包含：

- 本次 comparison-set-id；
- 参与对比的全部 run-id 及其顺序；
- 参与对比的全部 Git commit 信息；
- 实际执行的对比对，例如“当前 vs 上一次”“当前 vs baseline”或多 run 的相邻逐次比较；
- 实际采用的评估快照；
- 共同可比 dataset；
- 每个 comparison pair、dataset 和指标的左侧值与右侧值；
- 绝对差；
- 百分比变化；
- 每项指标跨 dataset 的平均百分比；
- 有效、失效和不可比数量。

#### 权限

- 当前和历史评估 JSON：只读权限；
- 当前和历史 run 元数据：只读权限；
- 自动对比需要当前 run 的 `comparisons/<comparison-set-id>` 目录读写权限；
- 临时对比需要算法根 `comparisons/<comparison-set-id>` 目录读写权限；
- 不需要算法源码、build_dir 或 dataset 写权限；
- 不允许修改历史评估快照或历史报告。

#### Dataset 需求

对比阶段不重新读取原始 dataset，直接读取已保存评估 JSON。

只比较：

- 同一个算法；
- 按 2.1.3 自动生成的 dataset-id 和 dataset 版本一致；
- 双方算法运行成功；
- 双方评估成功；
- 指标名称和定义一致；
- voeval 版本和评估配置一致。

若评估口径不一致，需要先回到评估功能，基于保存的算法输出和对应 dataset 重新生成统一口径评估快照。

full-report.json 不直接作为对比输入；需要新增详细指标时，先通过版本化规则归一化到 metrics.json，再进入本功能。

#### 成功与失败边界

- 没有参考 run 时，对比状态为 not_available，不算运行失败；
- 没有共同可比 dataset 时，输出不可比清单，不算算法失败；
- 单项指标失效时跳过该项并记录原因，不影响其他指标；
- 对比异常不得破坏已经完成的构建、运行和评估结果。

### 2.7 功能 6：输出存储

#### 输入

输出存储功能在 run 生命周期内持续管理前五项功能产生的全部状态和产物，而不是在最后一次性接收：

- run 元数据；
- 原始算法配置和合并 CLI 覆盖项后的 resolved 配置；
- git/commit.json；
- build 日志、状态和 manifest；
- 每个 dataset 的运行日志、状态和 raw-output；
- 每个 dataset 的评估快照；
- 一个或多个 comparison set 结果；
- 用户配置的 results_root；
- 用户配置的 min_free_bytes。

#### 处理过程

- **步骤 1：** run 开始前校验 results_root 可写且剩余空间满足最低要求；
- **步骤 2：** 在内存中生成唯一 run-id；
- **步骤 3：** 以 operation_type=start_run 原子创建 `<results-root>/.pipeline.lock`，记录 operation-id、host、pid、algorithm-id、run-id 和开始时间；已有有效锁时拒绝启动，不排队；
- **步骤 4：** 创建 run-id 目录和 manifest；
- **步骤 5：** 保存原始算法配置和解析后的 resolved 配置快照；
- **步骤 6：** 为 Git、build、每个 dataset、evaluation、comparison set 和 report 创建隔离目录；
- **步骤 7：** 每个阶段完成后原子写入状态；
- **步骤 8：** 每个 dataset 运行和评估完成后立即写检查点；
- **步骤 9：** 全部 dataset 结束后读取已保存结果形成集中汇总；
- **步骤 10：** 创建唯一 report-id，生成 Pipeline summary.json 和 summary.html；
- **步骤 11：** 建立各阶段文件索引和相对路径；
- **步骤 12：** 无论成功还是失败都保存当前已取得的信息；
- **步骤 13：** 完成所有必需 JSON 后才能把 lifecycle 标记为 completed；
- **步骤 14：** 正常结束、取消或已保存的失败退出时释放 .pipeline.lock；异常崩溃留下的锁只能在确认原 pid 不存在后由恢复命令接管。

#### 输出

统一目标目录：

```text
<results-root>/<algorithm-id>/runs/<run-id>/
├── manifest.json
├── status.json
├── config/
│   ├── algorithm.yaml
│   └── resolved.json
├── git/
│   ├── commit.json
│   ├── status.txt
│   ├── submodules.json     # 有 submodule 时
│   ├── staged.patch        # 暂存区有修改时
│   ├── unstaged.patch      # 已跟踪文件有未暂存修改时
│   └── untracked.json      # 有未跟踪文件时
├── build/
│   ├── status.json
│   ├── configure-command.json # 仅 cmake
│   ├── configure.log          # 仅 cmake
│   ├── build-command.json     # cmake 或 bash
│   ├── build.log              # cmake 或 bash
│   ├── environment.json
│   ├── artifacts/             # artifact_storage=copy 时
│   └── manifest.json
├── datasets/
│   └── <dataset-id>/
│       ├── input.json
│       ├── run/
│       │   ├── command.json
│       │   ├── status.json
│       │   ├── metadata.json
│       │   ├── attempts/
│       │   │   └── <attempt-no>/
│       │   │       ├── command.json
│       │   │       ├── stdout.log
│       │   │       ├── stderr.log
│       │   │       ├── status.json
│       │   │       └── output/
│       │   └── raw-output/
│       └── evaluations/
│           ├── index.json
│           └── <evaluation-id>/
│               ├── input-map.json
│               ├── input/
│               │   ├── data/
│               │   └── log/
│               ├── status.json
│               ├── config.json
│               ├── evaluation.log
│               ├── metrics.json
│               └── full-report.json    # save_full_report=true 时
├── comparisons/
│   ├── index.json
│   └── <comparison-set-id>/
│       ├── sources/            # 包含导入报告时
│       ├── status.json
│       ├── comparison.json
│       ├── incompatible-items.json
│       ├── comparison.log
│       └── comparison.html
└── reports/
    ├── index.json
    └── <report-id>/
        ├── summary.json
        └── summary.html
```

#### 权限

- results_root：创建目录、读写文件、原子重命名权限；
- results_root 根目录：创建和释放 .pipeline.lock 的权限；
- 当前 run-id 目录：读写权限；
- 历史 run：默认只读，不能覆盖；
- 算法首次接入或显式更新配置时，允许原子写 algorithm.json；
- 用户显式设置 baseline/reference 时，允许原子更新算法根目录的 baseline.json 或 references.json；
- dataset：只读，默认不复制、不修改；
- repo_dir 和 .git：只读；
- build_dir：构建功能可写；
- 不自动删除历史结果，清理必须由用户明确发起。

#### Dataset 需求

存储层不解析 dataset 内容，但必须在 input.json 和 manifest.json 中记录：

- dataset 原始绝对路径；
- dataset-id；
- dataset 版本；
- 必需输入文件校验结果；
- 算法运行状态；
- 评估状态；
- 实际采用的 evaluation-id；
- 不可比原因。

#### 成功与失败边界

- results_root 不可写或可用空间小于 min_free_bytes 时，在构建和运行前拒绝启动；
- 中途存储失败时停止继续产生大体量输出，并尽力写入 storage_failed 状态；
- HTML 生成失败不能删除 JSON、日志或算法原始输出；
- manifest.json、status.json 或 summary.json 未成功落盘时，run 不能标记为完成；
- 后一次 run、重评或重新对比不得覆盖历史文件。

### 2.8 六项功能的权限总表

| 功能 | repo/.git | build_dir | dataset | 当前 run 目录 | 执行权限 | 网络或 root |
| --- | --- | --- | --- | --- | --- | --- |
| 构建编译 | 源码只读 | 读写 | 不需要 | 写 build 日志和 manifest | 编译器、CMake 或 build.sh | 默认不需要 |
| 记录 Git commit | 只读 | 不需要 | 不需要 | 写 git 元数据 | Git | 不需要 |
| 运行代码 | 源码只读 | 读取产物 | 只读 | 写日志和 raw-output | 算法程序或 run.sh，按需 GPU | 默认不需要 |
| 评估结果 | 不需要 | 不需要 | 只读 | 写 evaluation 快照 | Python/voeval，HTML 按需 Node.js | 不需要 |
| 对比结果 | 不需要 | 不需要 | 默认不读取 | 写 comparisons | Pipeline 进程 | 不需要 |
| 输出存储 | repo 只读 | 读取 manifest | 只记录路径 | 读写全部当前 run 文件 | Pipeline 进程 | 不需要 |

### 2.9 当前实现与目标功能的分界

本节定义的是项目最终需要实现的功能边界，不代表当前代码已经全部具备。当前仓库与目标需求的关系如下：

| 能力 | 当前代码情况 | 本需求要求补齐的目标 |
| --- | --- | --- |
| 算法配置 | 已有 YAML、预编译 executable 和 command_template | 增加 repo、构建、Git、voeval 和统一存储契约 |
| 构建编译 | 只校验预编译 executable；没有通用 CMake/build.sh 构建流程 | 实现功能 1；同时保留 method=none 兼容预编译程序 |
| Git 记录 | 尚未形成 run 级 Git 快照 | 实现功能 2，只读记录当前 HEAD 和工作树，不自动切换版本 |
| Dataset 输入 | 当前从 YAML 的 dataset.root 递归扫描 | 改为 CLI 手动传入一个或多个 dataset 绝对路径 |
| 算法运行 | 已有命令模板和外部数字日志目录 | 迁移到 run-id 下的逐数据集隔离目录，并绑定构建产物 |
| 评估 | 当前是全部运行后批量调用 MATLAB/evo | 改为每个 dataset 运行成功后立即调用 voeval 并保存快照 |
| 汇总输出 | 当前主要是 checkpoint 和 Excel | 改为统一 run 目录、结构化 JSON 和 Pipeline HTML 汇总 |

因此，现有 YAML、命令模板执行和运行元数据可以复用；CMake/Bash 构建契约、Git 快照、手动路径输入、逐数据集即时 voeval、统一 run-id 目录和 JSON/HTML 汇总属于需要新增或重构的能力。现有配置中的外部绝对路径只代表原工作站示例，不能视为当前机器上已经可用的输入。

## 3. Pipeline 与 voeval 的职责边界

| 组件 | 负责 | 不负责 |
| --- | --- | --- |
| Pipeline | 接收算法选择和数据集路径、创建 run、只读记录 Git 身份、执行已声明的构建契约、调用算法、遍历数据集、调用 voeval、保存逐数据集结果、执行历史对比、形成集中汇总并保存历史 run | 不监听 GitHub、不自动切换代码版本、不自动发明或修改构建/运行脚本，不重新实现 ATE、RPE、Sim3 等单数据集评估算法，不拼装 voeval 的详细报告 |
| voeval | 一个算法输出在一个数据集上的输入解析、坐标与时间处理、轨迹对齐、指标计算、逐帧明细和单数据集可视化 | 不选择算法，不遍历多个数据集，不管理 run 历史，不生成 Pipeline 的多数据集汇总 |

目标系统完成后，voeval 将作为 Pipeline 唯一的算法结果评估工具；当前 Pipeline 代码仍使用 MATLAB/evo，接入 voeval 属于本需求要求的改造。当前 voeval 工具已经具备的正式文件入口为 sf_vo 和 sf_vloc。TUM 文件入口尚未接入 CLI 或本地服务，只保留为以后扩展方向，不作为当前已具备能力。

Pipeline 只读取 voeval 的结构化评估结果，不能从终端显示文本中猜测指标，也不得重新实现或修改 voeval 的评估算法。

## 4. 核心对象

| 对象 | 含义 |
| --- | --- |
| 算法配置 | 一个已接入算法的仓库路径、构建契约、运行命令或脚本、数据集路径传入方式、输出约定、voeval 配置和存储位置 |
| run | 用户手动选择一个算法和一组数据集路径后发起的一次完整运行 |
| run-id | 系统为每次 run 创建的唯一标识 |
| Git 快照 | 本次 run 开始时只读记录的 commit、分支、工作树和 submodule 状态 |
| 构建记录 | 构建命令、日志、状态、build_dir 和实际产物校验信息 |
| 数据集输入 | 用户本次提供的一个本地数据集路径 |
| 数据集运行结果 | 算法在一个数据集路径上的状态、日志和原始输出 |
| 评估快照 | 对一个已保存算法输出执行一次 voeval 后形成的不可变结构化结果 |
| 数据汇总 | 全部输入路径处理完成后，由 Pipeline 收集逐数据集状态和评估快照形成的本次 run 汇总 |
| 成功 run | lifecycle 已完成，且被选 report-id 的 outcome=success，可作为“上一次成功 run”、baseline 或参考对象的 run |
| baseline run | 用户用于长期对照的成功 run 报告快照，引用 run-id、report-id 和 evaluation-id 集合 |
| 参考 run | 用户额外保存的零个、一个或多个同算法历史结果快照 |

用户重新发起同一个算法和同一组数据集路径时，产生新的 run-id，不能覆盖原 run。工作站或进程中断后继续未完成工作时，恢复原 run-id。

## 5. 端到端主调用链

```text
阶段 0：预先接入一个具有明确构建、运行、输出和 voeval 契约的算法
                     ↓
阶段 1：用户手动启动 CLI
                     ↓
阶段 2：用户选择算法并提供 repo/config、构建覆盖项和一个或多个数据集路径
                     ↓
阶段 3：系统校验输入、结果目录和权限，创建唯一 run-id
                     ↓
阶段 4：只读记录当前 Git HEAD 与工作树状态
        ├─ 无法追溯 → 保存失败状态并停止
        └─ 记录成功 → 进入构建
                     ↓
阶段 5：执行算法已声明的构建契约并校验产物
        ├─ 构建失败 → 保存构建日志和失败报告并停止
        └─ 构建成功 → 冻结构建 manifest
                     ↓
阶段 6：按用户输入顺序遍历数据集路径
        ├─ Dataset 输入无效
        │    → 保存 dataset_input_invalid，不运行算法
        │    → 继续下一个数据集
        ├─ 运行算法失败
        │    → 保存失败状态和日志
        │    → 继续下一个数据集
        │
        └─ 运行算法成功
             → 立即保存算法原始输出
             → 立即调用 voeval
                  ├─ 评估成功 → 保存评估快照
                  ├─ 评估失败 → 保存评估未完成状态
                  └─ 不可评估 → 保存原因和不可评估状态
             → 写入本数据集检查点
             → 继续下一个数据集
                     ↓
阶段 7：全部数据集路径处理完成
                     ↓
阶段 8：读取已经保存的逐数据集结果，形成数据汇总
                     ↓
阶段 9：按用户选择执行历史 run 对比
                     ↓
阶段 10：生成 Pipeline HTML 与 JSON 报告并完成存储
```

主调用链会读取本地 Git 信息用于追溯，但不存在 GitHub/push 监听、自动发现 commit、commit 任务创建、自动 checkout、算法队列或后台调度。

## 6. 算法接入配置

### 6.1 用户需要提供的内容

接入一个算法时，用户至少需要提供：

- 算法名称和唯一标识；
- 本地 Git 仓库 repo_dir；
- 构建方式以及 source_dir、build_dir；
- CMakeLists.txt、build.sh 等构建入口，或 method=none 时的预编译产物；
- 运行所需且已经准备好的编译器、依赖、Python 环境和 GPU 环境；
- 默认运行命令或脚本；
- 如何把一个数据集路径传给算法；
- 算法输出目录和输出文件约定；
- 单数据集运行超时时间和最大重试次数；
- 对应的 voeval 模式；
- voeval 所需输入路径映射；
- voeval 评估配置；
- 算法输出格式说明。

算法输出格式必须先得到 voeval 支持，才能接入 Pipeline。

### 6.2 系统在运行前的边界

Pipeline 负责执行用户已经声明的构建和运行契约，但不负责：

- 获取算法代码；
- 切换代码版本；
- 自动准备未知依赖；
- 分析代码变化；
- 自动生成、猜测或修改 CMake、build.sh、run.sh 和算法源码。

用户需要保证源码、构建入口、依赖和运行环境在本地工作站上已经可用；Pipeline 按配置执行构建，而不是替用户设计构建方式。系统可以在算法接入时使用一个用户提供的轻量测试 dataset 验证以下流程能够跑通：

```text
读取算法配置
→ 记录 Git 快照
→ 执行声明的构建命令并校验产物
→ 运行算法
→ 产生约定输出
→ 调用 voeval
→ 保存评估结果
→ 形成单次汇总
```

验证未通过的算法不能作为可选算法进入正式运行。

## 7. 手动创建 run、记录 Git 与构建

### 7.1 用户输入

用户通过 CLI 手动提供：

- 本次选择的算法；
- 算法配置文件路径；
- 本地 repo_dir，以及需要覆盖时的 source_dir、build_dir 或构建参数；
- 一个或多个本地数据集路径；
- 可选的 run 名称；
- 可选的算法版本标签；
- 可选的本次修改说明或备注；
- 本次需要覆盖的算法运行参数；
- 本次需要覆盖的 voeval 评估配置。

算法版本标签和修改说明仍由用户填写；系统另外只读记录当前 Git commit 和工作树状态用于追溯，但不据此自动决定是否运行。

### 7.2 数据集路径规则

- 数据集直接以本地路径方式传给系统；
- 一次 run 可以提供一个或多个路径；
- 系统按照用户输入顺序处理；
- 系统需要将路径规范化为绝对路径并记录在 run 中；
- 开始运行前需要校验路径存在且可读；
- 路径无效时，不启动本次 run，并明确列出无效路径；
- 本次输入的路径列表就是本次 run 的计划数据集集合；
- 系统不维护固定回归数据集目录，也不需要数据集增加、删除和队列更新功能。

默认使用规范化后的数据集路径作为数据集匹配标识。若同一路径下的数据内容、真值、标定或关键配置发生变化，用户需要使用新的路径或显式提供新的数据集版本标识，避免新旧内容被当作同一数据集比较。

“启动前路径无效”和“run 创建后输入失效”是两个状态：前者属于整次请求的输入校验失败，不创建 run；后者按 dataset_input_invalid 保存到已创建 run 中并继续处理其他路径。

为了支持历史重新评估，历史结果引用的数据集路径及其对应版本需要保持可访问；至少要保证当时使用的原始数据、真值、标定和关键配置仍可取得。

### 7.3 创建 run 记录

输入校验通过后，系统创建唯一 run-id，并保存：

- 算法标识；
- run 名称和用户备注；
- 用户提供的算法版本标签；
- 本地仓库路径和 Git 快照位置；
- 构建配置、build_dir 和预期产物；
- 创建时间和开始时间；
- 数据集绝对路径列表及顺序；
- 算法运行配置；
- voeval 版本和评估配置；
- 本次输出目录。

系统随后依次完成 Git 记录和构建；构建成功后立即进入逐数据集运行，不进入任务队列，也不等待后台调度。

### 7.4 记录本次代码身份

创建 run 目录后，系统按功能 2 的契约读取当前本地 Git HEAD、分支、工作树和 submodule 状态，并写入 git/。这一阶段不访问 GitHub、不拉取代码、不切换版本，也不根据 commit 判断是否应当运行。

Git 记录失败时，本次 run 停止在 git_record_failed，保留已经创建的 run 元数据和错误日志，不进入构建。

### 7.5 构建并冻结运行产物

Git 快照保存成功后，系统按功能 1 的 cmake、bash 或 none 契约处理构建。构建成功后将实际 artifact 路径和校验值写入 build/manifest.json，后续所有 dataset 必须使用该 manifest 指向的同一组产物。

构建失败时，本次 run 停止在 build_failed，保存构建命令、标准输出、错误输出和已产生的文件，不进入任何正式 dataset 的算法运行或 voeval 评估。

## 8. 逐数据集运行与立即评估

Pipeline 按用户提供的路径顺序逐个处理数据集。

### 8.1 算法调用

对每个数据集路径，Pipeline 执行：

1. 校验 dataset 路径和算法声明的必需输入；
2. 校验并读取本次 build/manifest.json 中冻结的运行产物；
3. 根据算法配置构造本数据集的运行命令；
4. 在独立 attempt 目录中调用算法脚本或程序；
5. 应用单数据集超时时间和最大重试次数；
6. 保存每次尝试的标准输出、错误输出、退出状态和运行日志；
7. 检查当前尝试的约定输出是否已经生成；
8. 保存数据集运行状态。

### 8.2 Dataset 输入无效

dataset 路径在创建 run 时存在且可读，但处理到该路径时已经消失、变得不可读，或缺少算法声明的 imu、图像、标定或其他必需输入，记为 dataset_input_invalid：

- 不启动算法；
- 不调用 voeval；
- 不计入算法运行失败数量或失败率；
- 保存缺失文件清单后继续下一个 dataset；
- 本次 run 最终可以完成并生成部分报告，但不能标记为成功 run，也不能自动成为 baseline。

### 8.3 算法运行成功与失败

以下情况判定为“算法运行失败”：

- 算法进程异常退出；
- 算法运行超时；
- 达到最大重试次数后仍无法完成；
- 没有生成约定输出文件；
- 约定输出文件为空。

算法进程正常结束，并且约定输出存在且非空时，判定为“算法运行成功”。

算法运行失败后：

- 保存失败状态、失败原因和日志；
- 不调用 voeval；
- 不阻止本次 run 继续处理下一个数据集。

算法运行成功后：

- 立即保存算法原始输出；
- 在进入下一个数据集前，立即执行该数据集的 voeval 评估。

voeval 评估是单数据集循环的一部分，不能推迟到所有算法运行结束后再统一执行。

### 8.4 调用顺序

每个数据集必须严格按照以下顺序处理：

```text
校验 dataset 输入
→ 运行算法
→ 保存算法运行状态
→ 保存算法原始输出
→ 调用一次 voeval
→ 保存评估状态和结构化结果
→ 写入该数据集完成检查点
→ 进入下一个数据集
```

只有算法运行成功的数据集才调用 voeval。

### 8.5 voeval 输入与输出

Pipeline 根据算法配置和当前数据集路径，为 voeval 提供：

- 当前数据集对应的 data_dir；
- 当前算法输出对应的 log_dir；
- sf_vo 或 sf_vloc 模式；
- 本次评估配置。

每次 voeval 成功执行后，系统立即保存一个独立评估快照，至少包括：

- 数据集路径和版本标识；
- 算法输出位置；
- voeval 版本；
- 评估配置；
- 评估开始和结束时间；
- 结构化指标；
- 评估状态。

### 8.6 评估失败与不可评估

算法已经成功运行，但 voeval 输入解析或指标计算失败时：

- 算法运行状态保持成功；
- 评估状态记录为“评估未完成”；
- 保存评估错误和日志；
- 不计入算法运行失败数量和失败率；
- 保留算法原始输出；
- 继续处理下一个数据集。

voeval 或评估配置修复后，用户可以基于已经保存的算法原始输出单独重新评估，不得要求重新运行算法。

每次重新评估产生新的评估快照，原有评估结果继续保留，不得被覆盖。

无真值、缺少评估必需标定、模式不受支持或当前 dataset 在业务上没有可用评估口径时，记录 evaluation_unavailable，而不是评估异常：

- 算法运行状态保持成功；
- 不计入算法失败率；
- 单独保存不可评估原因并继续下一个 dataset；
- 本次 run 可以完成并生成部分报告，但不能标记为成功 run 或成为 baseline。当前项目中每个被选 dataset 都要求评估，不提供跳过评估的例外开关。

### 8.7 voeval 详细报告边界

voeval 可以针对单个数据集生成轨迹、逐帧误差、状态曲线和交互报告，但该报告不属于 Pipeline 汇总报告。

Pipeline 不嵌入、不拼装也不重新渲染 voeval 的详细 HTML。Pipeline 可以把 full-report.json 作为不可变评估快照保存，但汇总只读取 metrics.json。用户需要查看某个数据集的轨迹和逐帧详情时，使用保存的算法原始输出和 evaluation 配置单独运行 voeval。

## 9. 全部数据集完成后集中汇总

### 9.1 汇总触发时机

只有当本次输入的全部数据集路径都已经进入以下某一种状态后，才开始集中汇总：

- 算法运行成功且评估成功；
- 算法运行成功但评估未完成；
- 算法运行成功但不可评估；
- 算法运行失败。
- dataset 输入无效。

如果用户取消整个 run，或者系统异常导致本次 run 被明确结束，系统仍需要基于已保存内容生成部分汇总，并单独列出尚未处理的数据集路径；部分汇总不能标记为完整 run。

最终汇总只读取逐数据集阶段已经保存的状态、日志索引和评估快照，不重新运行算法，也不为了汇总再次调用 voeval。

### 9.2 数据汇总内容

本次 run 的数据汇总至少需要包含：

- 算法名称；
- run-id；
- run 名称、版本标签和用户备注；
- Git full hash、分支和 dirty 状态；
- 构建状态、build_dir、实际 artifact 和校验值；
- 本次输入的数据集路径总数；
- 尚未处理的数据集路径数量和路径列表；
- dataset 输入无效的数量、路径和原因；
- 算法运行成功的数据集数量；
- 算法运行失败的数据集数量；
- 算法运行失败的数据集路径和失败原因；
- 评估成功的数据集数量；
- 评估未完成的数据集数量；
- 评估未完成的数据集路径和原因；
- 不可评估的数据集数量、路径和原因；
- 每个数据集采用的评估快照；
- 每个数据集的关键指标；
- 本次算法失败率；
- 本次 run 的总体状态。

### 9.3 失败率与总体状态

算法失败率按照以下公式计算：

**算法失败率 = 算法运行失败的数据集数量 / 实际启动算法的数据集数量**

规则如下：

- 只统计算法运行失败；
- 实际启动算法的数据集数量等于算法运行成功数量加算法运行失败数量；
- dataset_input_invalid、尚未处理、取消、Git/构建/存储失败的数据集不进入分子或分母；
- 分母为 0 时失败率写 null，本次 run 不能判定为成功；
- voeval 失败不计入；
- 系统异常不计入；
- 用户取消不计入；
- 最大允许失败比例由算法配置；
- 失败率超过阈值时，本次 run 的 outcome 判定为 algorithm_failed；
- 失败率未超过阈值时，可以形成运行汇总，但必须标出失败数据集；
- 存在 dataset_input_invalid、评估未完成、evaluation_unavailable 或尚未处理数据集时，outcome 为 incomplete，不能作为成功历史 run；
- Git、构建、artifact、配置、系统或存储失败时，outcome 为 infrastructure_failed；
- 只有 Git 与构建成功、所有路径进入确定状态、没有上述 incomplete 条件、失败率不超过阈值，且所有要求评估的算法成功数据集均评估成功时，outcome 才是 success。

run 的执行状态和业务结果必须分开记录：

- lifecycle_state=completed 表示流程已经结束并生成当前可用汇总，不代表测试成功；
- 某个 report-id 的 outcome=success 才表示该 run 的这一组固定评估快照可以成为“上一次成功 run”、baseline 或参考对象；
- cancelled 或 interrupted 表示流程没有正常完成，即使已有部分报告也不能伪装成 completed success。

初次汇总把 outcome 同时写入 initial_outcome 和 current_outcome。用户修复 voeval 后显式重评并生成新 report-id 时，系统基于所选新 evaluation-id 重新计算该报告的 outcome；若变为 success，可以原子更新 current_outcome/current_report_id，并允许用户把这份 report 快照设为 baseline。该操作不改变算法原始输出、initial_outcome 或任何旧报告。

同时满足多个异常条件时，outcome 优先级固定为 infrastructure_failed > algorithm_failed > incomplete > success；报告仍需逐项保留所有次要问题，不能因最终 outcome 隐藏其他状态。

### 9.4 跨数据集汇总边界

不同数据集的场景、速度和指标量级可能不同，因此：

- ATE、RPE 等指标绝对值不得跨数据集直接求和或求平均；
- 汇总报告需要按数据集分别展示关键指标；
- 可以统计数据集运行和评估状态的数量；
- 只有在进行历史 run 对比时，才计算同一数据集、同一指标的变化百分比；
- 跨数据集的指标汇总只能基于各数据集的变化百分比，不能基于指标绝对值。

## 10. 手动历史结果对比

Git 自动触发不在本项目范围内，历史对比的基本对象是同一算法的 run；每个 run 同时带有 Git commit 信息用于识别代码版本。

### 10.1 对比对象

系统可以保留以下人工可选对象：

- 上一次成功且数据集兼容的 run；
- 用户指定的 baseline run；
- 用户配置的零个、一个或多个参考 run；
- 用户临时选择的历史 run；
- 用户临时导入的 Pipeline JSON 报告。

第一次成功 run 没有历史对象时，只生成当前数据汇总，并标记“暂无对比结果”。第一次成功完成全部输入路径并完成评估的 run 可以成为该算法的初始 baseline；用户可以之后更换 baseline。

参考 run 可以分别启用或停用。停用只影响后续报告是否默认包含该项对比，不删除历史结果，也不改写已经生成的报告。

### 10.2 可比性

只允许比较：

- 同一个算法；
- 按 2.1.3 自动生成的 dataset-id 相同；
- 数据集版本一致；
- 双方算法运行成功；
- 指标名称和定义一致；
- voeval 版本和评估配置一致。

两个 run 输入的路径列表不同时，只比较双方共同存在且兼容的数据集。

评估口径变化时，可以对保存的算法输出重新运行 voeval，形成使用相同口径的新评估快照，无需重新运行算法。

### 10.3 指标变化

对每个相同数据集和同名指标，计算：

**变化百分比 =（当前 run 指标值 - 对比 run 指标值）/ 对比 run 指标值 × 100%**

跨数据集汇总规则：

- 先分别计算每个数据集的同名指标变化百分比；
- 再对所有有效可比数据集的变化百分比等权求算术平均；
- 每个数据集权重相同；
- 不按照路径长度、帧数、速度、时长或匹配数加权；
- 同时展示平均百分比和各数据集百分比。

失效值规则：

- 缺失、null、NaN、正无穷或负无穷记为失效；
- 失效项不参与平均；
- 报告分别统计有效和失效数量；
- 平均结果后标注失效数量，例如 +4.8%（失效：2 个）；
- 数值 0 不能统一视为失效；
- 对比分母为 0 时的百分比展示方式留到设计阶段结合指标语义确定。

Pipeline 只标注指标变大、变小或不变，不自动判断算法改善、退化或回归通过。

### 10.4 多 run 与外部报告对比

用户可以选择两个或多个历史 run：

- 按 run 创建或完成时间排列；
- 默认逐次比较，即 B 对 A、C 对 B；
- 可以同时分别与 baseline 和用户选择的参考 run 对比；
- 不自动把当前 run 与全部历史结果逐一比较。

用户也可以导入两份或多份 Pipeline 结构化 JSON 进行临时对比。系统需要校验报告结构、算法、run-id、数据集路径与版本、评估快照和指标口径。

这种没有 current run 的临时对比保存到 `<results-root>/<algorithm-id>/comparisons/<comparison-set-id>/`，不写入任意历史 run。系统把导入 JSON 的不可变副本保存到 `sources/` 并记录 SHA-256。

不兼容时不得强行计算，需要列出不可比对象和原因。导入报告默认只用于当前临时对比；只有用户明确设置后，且其固定 report 快照 outcome=success，才能注册为 baseline 或参考对象。

## 11. Pipeline 汇总报告

### 11.1 报告类型

1. **单次运行汇总报告**
   - 展示当前 run 的输入路径、逐数据集运行状态、逐数据集评估状态、关键指标和集中汇总；
   - 存在兼容历史对象时，可以包含上一次成功 run、baseline 和已启用参考 run 的对比。
2. **多 run 对比报告**
   - 展示用户选择的两个或多个历史 run 或导入报告；
   - 展示逐次变化、统一参考对比和历史趋势。

### 11.2 单次运行报告内容

- 算法名称；
- run-id；
- run 名称；
- 用户提供的算法版本标签和修改说明；
- Git full hash、short hash、分支、dirty 状态和 Git 快照文件位置；
- 构建方式、构建状态、build_dir、实际 artifact、产物校验值和构建日志位置；
- 开始、结束时间；
- 算法运行配置；
- voeval 版本和配置；
- 本次输入的数据集绝对路径列表；
- 计划、尚未处理、dataset 输入无效、算法成功、算法失败、评估成功、评估未完成和不可评估数量；
- 尚未处理的数据集路径；
- 失败或未完成的数据集路径、原因和日志位置；
- 每个数据集采用的评估快照；
- 每个数据集的关键指标；
- 算法失败率和准确的 run 状态；
- 可用的历史对比结果；
- Pipeline 自己的数据汇总可视化。

报告不能把评估未完成、系统异常或用户取消写成算法运行失败。

Pipeline 的数据集明细只展示路径、状态、关键指标和可选变化百分比，不展示轨迹、逐帧误差或状态曲线。

### 11.3 报告产物

每类 Pipeline 报告同时生成：

- 可独立打开的 HTML；
- 可持久化、重新生成和导入比较的结构化 JSON。

每次生成报告都创建唯一 report-id，并写入 `reports/<report-id>/summary.html` 和 `summary.json`。HTML 和 JSON 必须表达同一次 run 和相同核心结果，并固定记录实际采用的 evaluation-id、comparison-set-id、schema_version 和生成配置。

首次 run 完成时生成一份初始报告。以后重评、重新选择参考对象或重新生成报告时创建新的 report-id，不改写旧报告。多 run 临时对比的 HTML 位于对应 `comparisons/<comparison-set-id>/comparison.html`，不伪装成某个 run 的初始汇总报告。

任务完成或失败后，第一版不通过邮件、即时通信工具或桌面通知主动提醒用户。用户通过 CLI 查看状态和报告路径。

## 12. 结果保存、查询与恢复

### 12.1 结果目录

```text
<results-root>/
├── .pipeline.lock          # 任一变更型操作期间存在；paused run 持续保留
└── <algorithm-id>/
    ├── algorithm.json
    ├── baseline.json
    ├── references.json
    ├── comparisons/        # 任意历史/导入报告的临时对比
    │   ├── index.json
    │   └── <comparison-set-id>/
    └── runs/
        └── <run-id>/       # 完整内部结构以 2.7 的统一目标目录为准
```

2.7 是 run 内部文件结构的唯一权威定义，本节不再复制第二棵目录树，避免后续修改时产生两套标准。algorithm.json 保存算法级元数据；baseline.json 和 references.json 只保存被选 run/report 的引用，不复制也不改写历史结果。

### 12.2 保存规则

- 每个 run 使用独立 run-id；
- Git 快照保存成功后才能开始构建；
- 构建 manifest 保存成功且 artifact 校验通过后才能开始正式 dataset 运行；
- 后一次 run 不得覆盖前一次；
- 每个数据集的算法运行完成后立即保存输出和状态；
- 每次 voeval 完成后立即保存评估快照；
- 后一次重新评估不得覆盖早期评估快照；
- 用户显式重评、重新对比或重新生成报告时，只能分别追加新的 evaluation-id、comparison-set-id 或 report-id；允许原子更新对应 index.json，但不得修改旧产物；
- 新 report 生成后允许原子更新 run 根 status.json 的 current_outcome/current_report_id；initial_outcome 和旧 report 保持不变；
- 最终汇总只能读取已经保存的逐数据集结果；
- 成功、失败、中断和取消的 run 都保留已有信息；
- 所有算法输出、日志、评估快照和报告默认长期保留；
- 系统不得按照时间、次数或磁盘容量自动删除；
- 存储清理只能由用户明确发起；
- 历史报告固定绑定原 run 和评估快照，不因 baseline 或参考 run 变化而改写；
- baseline.json 和 references.json 的更新只改变后续默认选择，不改变任何旧 run、comparison 或 report。

用户可以按照算法、run-id 和数据集路径查询运行状态、失败原因、算法输出、评估结果和汇总报告。

### 12.3 中断恢复

系统不维护待运行队列，但需要支持恢复一个已经开始且尚未完成的 run：

```text
工作站关机或 Pipeline 进程中断
→ 保留原 run-id 和逐数据集检查点
→ 用户重新启动 CLI 并选择恢复该 run
→ 逐数据集读取 run/status.json、raw-output 校验值和 evaluations/index.json
     ├─ 算法与评估均成功：直接跳过
     ├─ 算法成功且 raw-output 校验通过，但评估未完成：只创建新 evaluation-id 并重跑 voeval
     └─ 算法 attempt 未完成或 raw-output 未形成：创建新 attempt-no 并从头重跑算法
→ 继续后续数据集
→ 全部完成后生成集中汇总
```

用户重新发起一套新的运行时创建新 run-id；只有明确执行恢复操作时才继续原 run-id。恢复前必须重新校验 Git 快照、resolved 配置和全部 reference artifact；任一项变化时停止恢复并说明原因，不能悄悄重建或改用新代码。

## 13. 状态与异常规则

| 状态 | 判定 | 是否计入算法失败率 | 后续动作 |
| --- | --- | --- | --- |
| Git 记录失败 | HEAD 无法解析、ref 不一致、脏工作树策略拒绝或 Git 快照无法保存 | 否 | 保存错误并停止，不进入构建 |
| 构建失败 | configure/build 失败、超时、产物缺失或构建记录无法保存 | 否 | 保存构建现场并停止，不运行 dataset |
| artifact 变化 | reference 产物缺失或 SHA-256 与 manifest 不同 | 否 | 标记 artifact_changed 并停止，不自动重建或运行 |
| 配置错误 | 构建/运行入口、input_mapping、Python 或 voeval 版本契约无效 | 否 | 标记 configuration_error 并停止，提示用户修正配置 |
| Dataset 输入无效 | run 创建后路径消失/不可读，或缺少算法声明的必需输入 | 否 | 保存缺失清单，不运行算法，继续下一个 dataset |
| 算法运行成功 | 进程正常结束且约定输出存在、非空 | 否 | 立即调用 voeval |
| 算法运行失败 | 异常退出、超时、输出缺失或为空 | 是 | 保存后继续下一个数据集 |
| 评估成功 | voeval 完成并产生结构化结果 | 否 | 保存评估快照后继续 |
| 评估未完成 | 算法成功，但 voeval 解析或计算失败 | 否 | 保存错误后继续，可单独重评 |
| 不可评估 | 无可用真值、标定或受支持评估口径 | 否 | 保存原因后继续，outcome=incomplete |
| 系统异常 | 磁盘、GPU、驱动、Pipeline 或工作站问题 | 否 | 保存现场，用户修复后恢复原 run |
| 用户暂停 | 用户主动暂停当前 run | 否 | 保留检查点，等待手动恢复 |
| 用户取消 | 用户主动结束当前 run | 否 | 保留已有结果并生成当前可用汇总 |
| 对比不可用 | 无参考 run、无共同 dataset 或评估口径不兼容 | 否 | 输出原因，仍生成当前 run 报告 |
| 存储失败 | 必需状态、manifest 或 summary 无法落盘 | 否 | 停止继续产生大体量输出并保存可恢复现场 |
| lifecycle completed | 本次流程已结束且当前可用汇总已生成 | — | 根据独立 outcome 判断业务结果 |
| report outcome success | Git/构建成功、无 incomplete 条件、失败率不超阈值且该报告选择的所需评估均完成 | — | 该 run-id + report-id + evaluation-id 集合可作为上一次成功结果、baseline 或参考对象 |
| outcome algorithm_failed | 算法失败率超过阈值 | 仅实际算法失败 | 保留报告，不进入成功历史集合 |
| outcome incomplete | 存在输入无效、未完成评估、未允许跳过的不可评估、未处理或取消 | 否 | 保留部分报告，修复后重评、恢复或新建 run |
| outcome infrastructure_failed | Git、构建、artifact、配置、系统或存储失败 | 否 | 保存可恢复现场，不计为算法退化 |

### 13.1 暂停、取消和同时启动边界

- .pipeline.lock 是 results_root 下所有变更型命令的全局互斥锁，不只保护新 run；启动/恢复 run、重评、重新对比、重新生成报告、更新 algorithm/baseline/reference 都必须先获得该锁，并记录 operation_type 和目标 id；
- 只读查看状态、日志和已生成报告不需要获得写锁；
- 所有 index.json、status.json、baseline.json 和 references.json 的读-改-写必须在持锁期间使用原子替换完成；
- 第一版同一工作站、同一 results_root 只允许一个活动或暂停的 run；
- 第二个手动启动请求检测到有效 .pipeline.lock 时立即返回 run_already_active，不排队、不并行运行；
- 暂停采用安全边界语义：当前 Git、构建或“算法运行 + 立即评估”原子阶段完成并写入检查点后进入 paused，不挂起正在执行的子进程；
- 暂停的 run 保留活动锁；用户必须恢复或取消它，才能启动新 run；
- 构建、算法和 voeval worker 必须各自在独立进程组中运行；取消时向整个当前进程组发送 SIGTERM，等待 process.cancel_grace_seconds 后仍未退出则向进程组发送 SIGKILL，避免遗留子进程继续占用 GPU 或写文件；
- 被取消 attempt 的半成品只留在 `attempts/<attempt-no>/output/`，不得提升为 raw-output，不计为算法失败；
- 断电、崩溃或 Ctrl-C 发生在算法 attempt 内时，该 attempt 记为 interrupted，恢复时创建新的 attempt-no 重跑算法；若只发生在 voeval 阶段且 raw-output 校验通过，则保留算法成功状态并创建新的 evaluation-id，只重跑评估；
- cancelled run 不允许直接恢复；用户若要继续，需新建 run。interrupted 或 paused run 才允许恢复原 run-id。

不存在“等待队列”“队列优先级”或“继续下一个算法任务”状态。当前 run 结束后程序返回 CLI，由用户决定是否发起下一次 run。

## 14. CLI 操作范围

CLI 需要支持：

- 查看已经接入的算法；
- 查看某个算法的运行配置；
- 手动选择一个算法；
- 指定算法配置文件和本地 repo_dir；
- 可选覆盖 git.ref，但只做一致性验证、不自动 checkout；
- 查看或覆盖 source_dir、build_dir、构建方式及构建参数；
- 传入一个或多个数据集路径；
- 为 dataset 提供可选版本标识，并在启动前提示重复路径；
- 提供可选的 run 名称、算法版本标签和备注；
- 启动一次 run；
- 查看本次只读记录的 Git commit 和工作树状态；
- 查看构建状态、构建日志、artifact 路径和校验结果；
- 查看当前 run 的逐数据集进度；
- 暂停、恢复或取消当前 run；
- 恢复一个中断的历史 run；
- 查看指定 run 或数据集的状态和日志；
- 查看单数据集评估结果；
- 基于保存的算法输出重新运行 voeval；
- 重评、比较和重新生成报告时显式选择 evaluation-id，并显示新生成的 evaluation-id、comparison-set-id 或 report-id；
- 查看本次数据汇总；
- 打开或导出 Pipeline HTML 和 JSON 报告；
- 选择多个历史 run 进行手动对比；
- 导入多份 Pipeline JSON 进行临时对比；
- 设置 baseline report 快照；
- 添加、移除、启用或停用参考 report 快照。

已有活动或暂停 run 时，CLI 对新的启动请求直接显示当前 run-id 和 run_already_active，不创建等待项。

CLI 不包含 GitHub/push 监听、自动发现 commit、自动 checkout、自动触发、算法队列或任务优先级命令。

## 15. 需求验收主线

系统满足当前需求时，至少应能够证明：

1. 用户可以接入一个具有明确仓库路径、构建契约、运行命令、输出约定和 voeval 配置的算法。
2. 用户可以从 CLI 手动选择算法并提供一个或多个本地数据集路径。
3. 系统不访问 GitHub、不监听 push、不自动发现或切换 commit，也不创建算法任务队列；系统只读记录当前本地 HEAD 用于追溯。
4. 系统为本次手动运行创建唯一 run-id，并保存原始算法配置与 resolved 配置快照；run 中途修改外部 YAML 不影响本次执行。
5. 系统生成 git/commit.json，能够证明当前 commit、分支和工作树状态，并且不会修改源码或 .git。
6. 系统能够按 cmake、bash 或 none 契约完成构建校验，生成 build/manifest.json、日志和 artifact 清单，并证明运行入口来自 manifest 中的同一 artifact-id。
7. Git 记录或构建失败时，系统保存失败报告且不进入正式 dataset 运行；这些失败不计入算法失败率。
8. 系统按照输入顺序逐个运行数据集；dataset 输入无效不启动算法、不计入算法失败率，单个数据集算法失败后仍继续处理剩余路径。
9. 每个数据集算法运行成功后，系统在进入下一个数据集前立即调用 voeval。
10. 每个数据集的算法输出、运行状态、评估状态和评估快照都立即保存。
11. Git 记录失败、构建失败、artifact 变化、配置错误、dataset 输入无效、算法运行失败、评估未完成、不可评估、系统异常、存储失败和用户取消能够被准确区分。
12. 全部路径处理完成后，系统读取已经保存的逐数据集结果形成集中汇总，不重复运行算法或 voeval。
13. 汇总报告准确展示 Git、构建、路径总数、输入无效、算法成功和失败、评估成功、未完成和不可评估数量，以及具体路径和原因。
14. Pipeline 生成独立 HTML 与结构化 JSON 汇总报告，voeval 详细报告仍保持独立。
15. 工作站中断后，用户可以手动恢复原 run：算法输出已完整保存时只恢复评估，算法 attempt 未完成时才重跑算法。
16. 历史 run、算法输出、评估快照和报告不会被后续 run 覆盖。
17. 用户可以在同一算法内部手动选择兼容历史 run 进行对比；Git 信息仅用于识别版本，不替代 run 和评估快照的可比性判断。
18. source_dir 必须受 repo_dir 的 Git 快照约束；快照完成后源码发生变化会使本次构建失败，初始 dirty 与构建后变化不会被混为一谈。
19. copy 模式下后续运行使用 run 内冻结 artifact；reference 模式下每个 dataset 和恢复前重新校验 SHA-256，变化时停止且不计为算法失败。
20. 每次重试使用独立 attempt 目录，只能把本次成功 attempt 的输出提升为 raw-output，残留文件不能造成假成功。
21. Pipeline 能按 input_mapping 创建 voeval data/log 输入视图，正确处理 sf_vo 与 sf_vloc 固定文件名，并且不会修改 dataset 或算法原始输出。
22. metrics.json、comparison.json、summary.json 和导入报告均校验 schema_version；NaN/无穷转换为 null 并记录失效原因。
23. lifecycle_state、initial_outcome 和 current_outcome 分开记录；只有 outcome=success 的 report 快照才能成为上一次成功结果、baseline 或参考对象，成功重评可以更新 current 指针但不能改写旧报告。
24. 多次重评、重新对比和重新生成报告分别创建新的 evaluation-id、comparison-set-id 和 report-id，旧快照和旧报告保持不变。
25. 对比只对共同且口径兼容的 dataset 逐项计算百分比；不跨 dataset 平均 ATE/RPE 绝对值，失效项不参与百分比平均并显示失效数量。
26. 同一 results_root 的全部变更型命令共用全局锁；同时只允许一个活动或暂停 run，第二个启动请求被拒绝而不排队，暂停、取消和中断恢复符合 13.1 的进程边界。
