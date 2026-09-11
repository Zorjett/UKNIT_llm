# B 安全评估通用本地契约 v0

> 契约版本：candidate-spec-v0.3 / security-config-v0.3  
> 兼容版本：v0.2 继续严格表示原有4轮冻结夹具  
> 适用范围：64位、3～12轮、单路径差分与线性安全评估

## 1. 目的与边界

本契约使 B 能在不依赖 A 的 LLM 配置或 C 的未完成工程框架时，稳定读取完整候选并输出可复查的安全报告。未来 A/C 字段名不同时，只修改读取层或增加薄适配器，不改变 DDT、Walsh、SAT、优化器和见证检查器的数学语义。

本契约只描述低轮数、单密钥、单条差分／线性路径评估，不覆盖 differential hull、linear hull、积分、相关密钥、密钥恢复或完整轮数安全证明。

## 2. 文件接口

输入文件：

- `data/fixtures/candidate_valid.json`：合法、完整、确定的基准候选。
- `data/fixtures/candidate_invalid.json`：只含一个预期缺陷的非法候选。
- `data/fixtures/candidate_valid_summary.json`：合法候选的冻结摘要测试向量。
- `configs/security_smoke.json`：第一阶段项目候选 smoke 分析配置。
- `configs/paper_baseline.json`：完整 uKNIT-BC 论文窗口复现配置。
- `configs/project_candidate_4r.json`：兼容的4轮冻结配置。
- `configs/project_candidate_multiround.json`：3～12轮完整候选配置。

实现入口为 `src/llm_cipher/security/candidate_reader.py`，里程碑 0 自动测试为
`tests/security/test_candidate_reader.py`。读取器不得静默修复候选。

## 3. CandidateSpec 顶层字段

| 字段 | 类型 | 约束 |
| --- | --- | --- |
| `schema_version` | string | 新候选使用 `candidate-spec-v0.3`；兼容读取 `v0.2` 四轮夹具 |
| `candidate_id` | string | 人类可读标识；不参与候选哈希 |
| `candidate_hash` | string | `sha256:` 加 64 位小写十六进制摘要 |
| `block_size` | integer | 必须为 64 |
| `num_rounds` | integer | v0.3必须为3～12且等于 `rounds` 长度；v0.2必须为4 |
| `state_encoding` | object | 必须与第 4 节完全一致 |
| `round_layout` | string | 必须为 `ark-s-l_except_last-final_ark` |
| `rounds` | array | 按执行顺序排列的 `num_rounds` 个轮对象，不得重排 |

未知字段默认拒绝，以避免拼写错误被静默忽略。

## 4. 位序和轮布局

固定约定：

```text
block_size = 64
bit 0 = 最高位（MSB0）
nibble 0 = 16 位十六进制字符串最左侧半字节
external_state_format = hex16
round_layout = ARK -> S -> L，最后一轮省略 L，末尾再 ARK
```

对应 JSON：

```json
{
  "bit_numbering": "msb0",
  "nibble_order": "left_to_right",
  "external_state_format": "hex16"
}
```

轮密钥 XOR 不改变差分传播；本契约仍记录轮布局，以免论文窗口与项目候选语义混用。

## 5. 轮对象和 S 盒

每个轮对象恰好包含：

- `sboxes`：长度为 16；
- `linear_rows`：前 `num_rounds-1` 轮为64行矩阵，最后一轮必须为 `null`。

每个 `sboxes[i]` 是展开后的 16 项整数查找表：

- 长度必须为 16；
- 元素必须都是 0..15 的整数；
- 排序后必须等于 `[0,1,...,15]`；
- 必须属于 MANTIS 基础 S 盒的输入／输出位排列变体。

MANTIS 基础 S 盒为：

```python
[12, 10, 13, 3, 14, 11, 15, 7,
  8,  9, 1, 5,  0,  2, 4, 6]
```

交给 B 的正式候选必须包含展开后的查找表，不能只给排列描述，也不能要求 B 在父候选上继续叠加排列。

## 6. 线性层语义

`linear_rows[j]` 是输出 bit `j` 所依赖的输入 bit 索引集合：

```text
y[j] = XOR(x[i] for i in linear_rows[j])
```

约束：

- 必须有 64 行；
- 每行元素必须是 0..63 范围内互不重复的整数；
- 哈希规范化时对每一行的列索引升序排列，但 64 行本身不得重排；
- 矩阵必须在 GF(2) 上秩为 64；
- 当前合法夹具使用每行／每列重量均为 3 的可逆循环矩阵；
- 项目候选最后一轮 `linear_rows` 必须为 `null`。

本地夹具采用的已知基础矩阵命名为 `circulant_i_p_p2_v0`，定义为：

```text
linear_rows[j] = sorted({j, (j + 1) mod 64, (j + 2) mod 64})
M_fixture = I + P + P^2
```

该矩阵已经在测试中验证 GF(2) 秩为 64，且每行／每列重量均为 3。它只用于
B 独立阶段的 reader、哈希和位序稳定性测试，不冒充论文 uKNIT-BC 的正式线性层。
正式联调候选的基础矩阵集合需由团队冻结；B 的通用读取器仍接受所有满足本契约
结构要求且在 GF(2) 上可逆的 64×64 矩阵。

差分传播采用：

```text
y = Mx
```

线性掩码传播采用：

```text
alpha = M^T beta
```

其中 `alpha` 是线性层输入侧掩码，`beta` 是输出侧掩码。

## 7. 候选哈希规范

只对完整密码语义对象计算哈希。以下字段参与哈希：

```text
block_size
num_rounds
state_encoding
round_layout
rounds
```

以下字段不参与哈希：

```text
schema_version
candidate_id
candidate_hash
父候选、生成理由、时间、分数及其他非密码语义元数据
```

规范化步骤：

1. 构造只含上述语义字段的新对象；
2. 每个 `linear_rows[j]` 内的列索引升序排列；
3. `rounds` 顺序、每轮 16 个 S 盒顺序和矩阵 64 行顺序保持不变；
4. JSON 键按字典序排列；
5. UTF-8 编码，`ensure_ascii=False`；
6. 分隔符使用 `(",", ":")`，不保留无意义空格；
7. 计算 SHA-256，并添加 `sha256:` 前缀。

参考伪代码：

```python
payload = json.dumps(
    semantic_spec,
    ensure_ascii=False,
    sort_keys=True,
    separators=(",", ":"),
).encode("utf-8")
candidate_hash = "sha256:" + hashlib.sha256(payload).hexdigest()
```

B 必须自行复算。若输入自带哈希与复算结果不一致，返回 `invalid_input`，不得继续建模。

固定测试向量：

```text
candidate_valid.json   = sha256:39d6a811aeb60764f736867688c09fafe46cf20867f6cd2215171c0373d30873
candidate_invalid.json = sha256:a2a79672c917c520f05cc912feea86d03a4ba455b5b730d6461461fdc2dc14fa
```

非法夹具的哈希也与其文件内容匹配，确保测试首先命中预期的 S 盒错误，而不是哈希错误。

## 8. 非法夹具的单一预期错误

`candidate_invalid.json` 与合法夹具结构相同，但：

```text
rounds[0].sboxes[0][15] = 4
```

这会使第一个 S 盒重复输出 4 并缺少输出 6。预期读取结果：

```json
{
  "code": "invalid_sbox_permutation",
  "field": "rounds[0].sboxes[0]",
  "message": "S-box must be a permutation of 0..15"
}
```

## 9. SecurityConfig v0.3

`configs/security_smoke.json` 和 `configs/project_candidate_4r.json` 保留原有四轮
兼容语义。`configs/project_candidate_multiround.json` 使用：

- profile：`project_candidate_multiround`；
- scope：`full_candidate`；
- 模式：`differential` 和 `linear`；
- 起始轮：0；
- 轮数：`candidate`，运行时解析为候选实际的3～12轮；
- 输入端点：`nonzero_free`；
- 输出端点：`free`；
- 末线性层策略：`follow_candidate_spec`；
- Kissat 单查询超时：20 秒；
- 每模式总预算：60 秒；
- 最大搜索保护权重：`auto`，分别从实际DDT/Walsh有限转移推导；
- SAT 见证必须独立验证；
- 请求证明最优，但预算耗尽时必须诚实返回 bounded、feasible 或 unknown。

`maximum_weight` 只是搜索保护上限，不是安全结论。64位MANTIS变体网络的保守
上限为差分 `48R`、线性 `32R`，实现仍从实际S盒表计算而不硬编码这两个公式。
`max_memory_mb=null` 表示尚未真正实施内存限制，不得伪填数字。

## 10. 读取器验证顺序

建议按以下顺序返回第一个确定错误：

1. JSON 是否可解析；
2. 是否存在必需字段及是否有未知字段；
3. `schema_version`；
4. 顶层类型、`block_size`、`num_rounds`；
5. `state_encoding` 和 `round_layout`；
6. 轮数及末轮线性层策略；
7. 每轮 S 盒数量、元素范围和置换性；
8. S 盒是否属于允许设计空间；
9. 线性层行数、索引范围和行内重复；
10. GF(2) 秩与可选的行列重量／正交性策略；
11. 规范化并复算 `candidate_hash`；
12. 分析窗口是否越界。

任何错误都不得被静默修复。

项目候选分析窗口通过 `validate_analysis_window(candidate, start_round,
num_rounds)` 单独验证。`start_round` 必须是非负整数，`num_rounds` 必须是正整数，
并满足：

```text
start_round + num_rounds <= candidate.num_rounds
```

布尔值不能冒充整数。成功时返回 `(start_round, exclusive_end_round)`；越界返回
`analysis_window_out_of_range`，不得自动截断窗口。

线性矩阵当前的通用强制条件是格式正确且 GF(2) 秩为 64。合法本地夹具另行测试
每行／每列重量均为 3，但该重量尚未冻结为所有候选的通用拒绝条件。`M^T M=I`
正交性和“只能由指定基础矩阵做有限行／列交换”的轨道限制也尚未冻结，因此 reader
当前不擅自启用。团队以后启用任一限制时，必须把策略写进配置／schema 并增加对应
字段级错误测试。

## 11. 错误结构

```json
{
  "code": "singular_linear_matrix",
  "field": "rounds[1].linear_rows",
  "message": "matrix rank over GF(2) is 63; expected 64"
}
```

顶层执行状态使用：

```text
invalid_input
```

数学结论使用：

```text
unknown
```

非法输入不得填写虚假的权重。

## 12. reader 稳定性摘要

同一个合法候选重复读取两次，必须生成完全一致的摘要：

```text
schema_version
candidate_hash
num_rounds
每轮 16 个 S 盒的 SHA-256
每个非空线性层的 SHA-256
state_encoding
round_layout
```

B 独立阶段先保证自身两次运行一致；联调阶段再要求 A/B/C 输出完全一致。

本阶段冻结摘要保存在：

```text
data/fixtures/candidate_valid_summary.json
```

直接验证命令：

```bash
python src/llm_cipher/security/candidate_reader.py \
  data/fixtures/candidate_valid.json \
  --expect-summary data/fixtures/candidate_valid_summary.json
```

退出码为 0 表示候选合法、候选哈希正确且生成摘要与冻结夹具完全一致。

## 13. 两类分析配置必须分开

论文复现配置保存在 `configs/paper_baseline.json`：

```text
analysis_profile = paper_baseline
source_cipher = uknit-bc-12r
start_round = 0
num_rounds = 4
window_semantics = paper_W(i,r)
endpoint_constraints = nonzero_free_to_free
```

该文件中的 `paper_reference` 仅冻结论文 `W(0,r)` 的低轮数核对值：差分为
`2, 8, 14, 25`，线性相关权重为 `1, 4, 7, 13`。这些值不得套用到其他
起始轮，也不得套用到项目候选。

正式项目候选配置保存在 `configs/project_candidate_multiround.json`：

```text
analysis_profile = project_candidate_multiround
analysis_scope = full_candidate
round_layout = ark-s-l_except_last-final_ark
num_s_layers = candidate.num_rounds
num_linear_layers = candidate.num_rounds - 1
configured final_linear_policy = follow_candidate_spec
resolved candidate final_linear_policy = omitted_by_candidate_design
```

`follow_candidate_spec` 表示评估器服从候选文件；该候选的实际结构再解析为
`omitted_by_candidate_design`。这样同时保留通用配置行为和本候选的确定结构，不能
把“配置如何读取候选”和“候选最终有没有末线性层”混为一个概念。完整候选必须
从第0轮开始覆盖全部候选轮数；部分窗口必须使用单独的 `window` scope。

两个 profile 共用差分/线性模式、非零自由输入到自由输出、Kissat 路径和初始预算，
但 profile 身份字段严格分离：论文配置不得出现 `candidate_path`，项目候选配置不得
出现 `source_cipher`、`window_semantics` 或 `paper_reference`。

“论文前 4 轮的 S0..S3 和 L0..L2”只能称为 `published_prefix_r4` 或论文组件导出的
4 轮缩减种子，不能称为完整论文窗口的逐层原样复制。不得把论文完整密码的窗口名称
或安全数字直接套到项目 4 轮末轮无 L 候选上。

`max_memory_mb=null` 表示尚未实施内存限制；`optimization.maximum_weight=192` 只是
搜索保护上限，不是评估结果。配置文件不得预填 `best_weight`、上下界或结论。

## 14. v0.3 冻结项与允许变化

冻结项：

- 位序、nibble 顺序和外部状态格式；
- 轮布局；
- S 盒展开格式；
- 矩阵行语义；
- 差分／线性传播方向；
- 哈希规范化字段与算法；
- timeout 不等于 UNSAT；
- 见证必须独立复算。

联调时允许通过 schema 升级或薄适配器改变字段名称，但必须同步更新契约、测试向量和版本号。

## 15. 里程碑 0 通过状态

当前本地闸门已通过：

- 同一个合法候选连续读取两次产生完全相同的摘要；
- 摘要与 `candidate_valid_summary.json` 完全一致；
- 候选 SHA-256 可稳定复算；
- 三个非空线性层的 GF(2) 秩均为 64；
- 合法候选通过全部结构、位序、S 盒设计空间、矩阵和哈希检查；
- 非法夹具稳定返回 `invalid_sbox_permutation`，字段路径为
  `rounds[0].sboxes[0]`；
- smoke 配置固定了 4 轮、MSB0、末轮无 L、差分／线性双模式和 20/60 秒预算；
- `tests/security/test_candidate_reader.py` 的 8 项测试全部通过。
