# 使用说明

元 skill 用于创建项目技能；生成的项目 skill 用于执行反复任务。两者各有独立目录。安装脚本仅安装本仓库的元 skill，不自动安装生成包，不写用户全局配置。

## 快速验证流程

1. 安装元 skill 到已有项目，在 Codex 中调用 `$project-skill-builder`。
2. 指定反复任务、项目路径和输出目录。没有真实案例时，可以明确使用合成示例；它不会被计作真实检索。
3. Codex 读取项目并提出关键问题，然后检索、比较和生成包。缺少检索工具时会报告限制。
4. 检查来源、取舍、项目约束和测试结果。需要安装时，把整个生成包复制到项目的 `.agents/skills/<包名>/`。
5. 在后续任务中调用生成 skill。项目变化后，手动要求元 skill 刷新它。

## 脚本

以下 `SKILL` 表示元 skill 的目录，`BUNDLE` 表示生成包目录；请替换成实际路径。全部只需 Python 3.9+ 标准库。

```sh
python SKILL/scripts/validate_bundle.py validate BUNDLE
python SKILL/scripts/validate_bundle.py seal NEW_BUNDLE
python SKILL/scripts/project_snapshot.py snapshot --root PROJECT --files README.md summary.py --output SNAPSHOT.json
python SKILL/scripts/project_snapshot.py compare --before OLD.json --after NEW.json
python SKILL/scripts/refresh_bundle.py CURRENT PROPOSED
python SKILL/scripts/refresh_bundle.py CURRENT PROPOSED --apply
```

`seal` 只用于新包或已明确修改的提案，不能用于抹平现有包的人工修改。校验中的 `modified_files` 是人工变化提示，即使 `valid` 为 true 也需要查看。快照只读取显式指定的安全文件，保存相对路径与哈希。刷新默认只预览，`--apply` 才写；冲突时原包保持原样。成功更新前保留外部历史备份，默认在原包父目录的 `.project-skill-builder-history/`。

`references/user-overrides.md` 用于长期保留的人工补充。普通受管文件也可以手改，但刷新会进行三方比较：旧生成版本、当前本地内容、新提案。两方都变时停止，不自动判断哪方正确。用户新增文件与提案同名也会停止。

完整格式见 [bundle contract](../skills/project-skill-builder/references/bundle-contract.md)。校验器支持有限 YAML 前置元数据格式；它不替代 Codex 自身的完整 YAML 解析。

## 相关已有项目

这类能力已有公开实现。本项目选择适配软件项目中的反复任务，并提供明确的包结构、来源状态和保护人工修改的刷新脚本；这些取舍尚不代表独有优势。

| 项目 | 相关能力 | 当前取舍 |
| --- | --- | --- |
| [Vercel find-skills](https://github.com/vercel-labs/skills/blob/3694740352eeef5cdd689af694c485f1ff62eec3/skills/find-skills/SKILL.md) | 需求识别、候选搜索、推荐与安装 | 把热度作为线索，比较正文与项目硬约束后再选择 |
| [Anthropic skill-creator](https://github.com/anthropics/skills/blob/8a1541c4a3ffa5a20a5a91de0dcf3f0bab1d1ef4/skills/skill-creator/SKILL.md) | 访谈、研究、生成、行为评测与迭代 | 保留小规模对照思路，采用 Codex 包结构与标准库脚本 |
| [Superpowers writing-skills](https://github.com/obra/superpowers/blob/8ca22dba9a94f28898bbce59f2537ff4d87c747d/skills/writing-skills/SKILL.md) | 技能编写、基线、独立场景与验证 | 使用真实执行证据，避免把文字检查当行为验证 |

本仓库没有打包这些第三方 skill；其适用许可证和本次公开读取证据见[验证记录](validation.md)。

## 复现与发布

```sh
python -m unittest discover -s tests -v
python tools/package_project.py --destination "release/project-skill-builder" --archive "release/project-skill-builder.zip"
```

打包采用产品文件清单，排除工作目录、对话原文、设计过程和缓存。命令拒绝覆盖现有输出。打包不能代替密钥扫描；每次上传前仍需复查待上传文件、暂存区和实际将推送的提交。

公开仓库：[cloudwallker/project-skill-builder](https://github.com/cloudwallker/project-skill-builder)。中英文仓库简介：

> Build project-specific Codex skills through clarification, public skill research, comparison and safe manual refresh. | 通过需求澄清、公开 skill 研究、比较与手动刷新，生成项目专用 Codex skill。

首次发布可从 ZIP 解压后的根目录建立独立仓库；后续更新保留已有 Git 历史。Git 提交使用本人已核实的 GitHub 身份及已关联的 noreply 邮箱，不公开私人邮箱。
