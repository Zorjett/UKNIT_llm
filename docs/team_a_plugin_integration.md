# Team B → Team A 3～12轮适配说明

## 范围

该适配器接受 Team A Plugin API 1.0 的3～12轮、64位候选。每轮使用16个
4位S盒，前 `R-1` 轮具有可逆线性层，最后一轮省略线性层。2轮、13轮及其他
范围外轮数会在启动SAT前返回 `status=unavailable`，不会产生或伪造安全分数。

入口文件是 `team_plugins/security_evaluator.py`，核心适配逻辑是
`src/llm_cipher/security/team_a_adapter.py`。

## 安全结果映射

- 差分与线性结果均为 `completed + optimal`：返回 `status=ok` 和两个真实权重。
- 任一模式为 bounded、feasible 或 timeout：返回 `status=unavailable` 和中性权重。
- invalid_input 或 solver_error：返回 `status=error`。
- 完整 `SecurityReport`、A 的 fingerprint 和 B 的内部 CandidateSpec hash 保存在
  `artifacts`，二者不会被假装成同一种哈希。

## 运行配置

适配器默认从 `PATH` 调用 `kissat`。可以使用以下环境变量覆盖：

- `UKNIT_B_SOLVER`
- `UKNIT_B_SINGLE_QUERY_TIMEOUT_S`，默认 5 秒
- `UKNIT_B_TOTAL_TIMEOUT_S_PER_MODE`，默认每种模式 20 秒
- `UKNIT_B_MAXIMUM_WEIGHT`，默认 `auto`，也可指定非负整数

同名配置也可以放在 Team A 传入的 `context["b_security"]` 中，键名分别为
`solver_executable`、`single_query_timeout_s`、
`total_timeout_s_per_mode` 和 `maximum_weight`。

## 合并到 Team A 仓库

1. 将本交付物的 `src/llm_cipher` 放入 Team A 仓库的 `src/llm_cipher`。
2. 用本交付物的 `team_plugins/security_evaluator.py` 替换 Team A 的同名占位文件。
3. 将 `tests/security` 放入 Team A 仓库并运行全部测试。
4. 在 Team A 仓库执行 `python -m pip install -e .`，或将 `src` 加入
   `PYTHONPATH`。
5. 在 Ubuntu/WSL 中设置 Kissat 路径，先进行3轮低成本联调，再按预算测试更多轮。

适配器按候选实际轮数执行完整候选分析。短预算下的 `timeout`、`bounded` 或
`feasible` 仍会返回 `status=unavailable`；这表示尚无可排名的已证最优权重，
并不表示安全权重为零。12轮能够建模和受控退出，不等于普通电脑能在短时间内
证明两种模式均为最优。

不要提交 `.venv`、`__pycache__`、CNF、solver 日志、`runs`、外部工具二进制或
阶段总结文档。
