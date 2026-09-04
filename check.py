from pathlib import Path
import json

from utils import pickle_load
from team_plugins.plugin_contracts import candidate_to_dict


run_dir = Path(
    r"D:\xinxijishujingsai\架构\uknit-constructions\runs\RUN_2026-09-04_19-47-31-917460"
)

generation = pickle_load(str(run_dir / "gen_5_4.pkl"))


def format_json(obj, level=0):
    """格式化 JSON：
    - 字典正常展开
    - 二维数组每个组件一行
    - rounds 中的每个 round 单独展开
    """

    indent = "  " * level
    next_indent = "  " * (level + 1)

    # =========================
    # 字典
    # =========================
    if isinstance(obj, dict):

        if not obj:
            return "{}"

        lines = ["{"]

        items = list(obj.items())

        for i, (key, value) in enumerate(items):

            formatted_value = format_json(value, level + 1)

            comma = "," if i < len(items) - 1 else ""

            # 多行内容
            if "\n" in formatted_value:
                lines.append(
                    f'{next_indent}"{key}": {formatted_value}{comma}'
                )
            else:
                lines.append(
                    f'{next_indent}"{key}": {formatted_value}{comma}'
                )

        lines.append(indent + "}")

        return "\n".join(lines)

    # =========================
    # 列表
    # =========================
    elif isinstance(obj, list):

        if not obj:
            return "[]"

        # -------------------------
        # 二维数组
        # 例如 sboxes:
        # [
        #   [1, 2, 3],
        #   [4, 5, 6]
        # ]
        # -------------------------
        if all(isinstance(x, list) for x in obj):

            lines = ["["]

            for i, component in enumerate(obj):

                # 一个组件内部全部放在同一行
                component_text = ", ".join(
                    json.dumps(x, ensure_ascii=False)
                    for x in component
                )

                comma = "," if i < len(obj) - 1 else ""

                lines.append(
                    f"{next_indent}[{component_text}]{comma}"
                )

            lines.append(indent + "]")

            return "\n".join(lines)

        # -------------------------
        # 列表中是字典
        # 例如 rounds:
        #
        # [
        #   {
        #     "round_index": 0,
        #     ...
        #   },
        #   {
        #     "round_index": 1,
        #     ...
        #   }
        # ]
        # -------------------------
        elif all(isinstance(x, dict) for x in obj):

            lines = ["["]

            for i, item in enumerate(obj):

                formatted_item = format_json(
                    item,
                    level + 1
                )

                comma = "," if i < len(obj) - 1 else ""

                lines.append(
                    formatted_item + comma
                )

            lines.append(indent + "]")

            return "\n".join(lines)

        # -------------------------
        # 普通一维数组
        # -------------------------
        else:
            return json.dumps(
                obj,
                ensure_ascii=False
            )

    # =========================
    # 基本类型
    # =========================
    else:
        return json.dumps(
            obj,
            ensure_ascii=False
        )


# =========================================================
# 生成每个 candidate 的 JSON
# =========================================================

for member in generation.members:

    candidate = candidate_to_dict(
        member,
        validate=True
    )

    output = run_dir / (
        f"{candidate['candidate_id']}.candidate.json"
    )

    output.write_text(
        format_json(candidate),
        encoding="utf-8"
    )

    print(output)