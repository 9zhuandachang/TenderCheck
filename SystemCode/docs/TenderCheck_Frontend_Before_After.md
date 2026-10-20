# TenderCheck 前端改版对比说明

比较日期：2026 年 10 月 20 日  
项目目录：`D:\qianduan\TenderCheck\SystemCode`  
文档用途：说明本次改版前后的页面、交互、导航和使用体验差异，供项目维护、演示及验收使用。

## 1 核心变化与比较范围

本次改版让 TenderCheck 更明确地表达“有证据可追溯的投标合规审查”，并将项目首页组织为一个能够告诉用户下一步该做什么的工作入口。最明显的功能变化是：已有项目时也能随时创建第二个项目；最明显的信息变化是：首页顶部有了依据实际数据生成的 `Next step`；最明显的排版变化是：欢迎页入口更加统一，完整历史和次要说明得到收纳。

原版本已经具备文档上传、需求确认、自动审核、证据核查、整改复检、签署和导出能力，也已有局部的前置条件提示。本次主要调整这些能力的展示和入口，没有重新实现审查算法。

本文中的“最初版本”指本轮修改开始前的代码，“当前版本”指全部首页与导航调整完成后的代码。对比包括欢迎页、项目 `Overview`、侧边栏和前端路由组织；其他业务页面的主体内容不在本次改版范围内。

### 1.1 对比依据

| 文件 | 最初版本依据 | 当前版本 |
| --- | --- | --- |
| 首页 | `backups/home-navigation-20261020-223656/src/app/views/overview.py` | `src/app/views/overview.py` |
| 公共界面组件 | `backups/home-navigation-20261020-223656/src/app/tcui/ui.py` | `src/app/tcui/ui.py` |
| 应用入口 | `backups/layout-polish-20261020-231132/src/app/main.py` | `src/app/main.py` |

已逐行核对以上三份基准备份，它们均与文档生成时 Git `HEAD` 中对应文件的内容一致。`main.py` 在前几轮修改中没有变化，因此使用它在最后一轮改版前的备份作为初始代码依据。

本文的布局示意表达代码中的组织结构，不是浏览器截图。行为结论依据源代码和已运行的测试；实际像素位置、不同浏览器的观感及具体屏幕宽度下的效果，仍需在浏览器中查看。

## 2 前后差异总览

| 对比项目 | 最初版本 | 当前版本 | 变化性质 |
| --- | --- | --- | --- |
| 首页定位 | 一段较长说明涵盖审核、证据、整改和签署 | 独立强调 `Evidence-based bid compliance review.`，随后补充来源页追溯和问题解决 | 文案优化 |
| 欢迎页可达性 | 通常只有系统中没有项目时出现 | 无项目时出现，也可以通过侧边栏 `New project` 主动进入 | 新增入口 |
| 新建项目卡片 | 外部卡片和内部表单各有边框 | 保留卡片外框，表单设为无边框 | 排版优化 |
| 新建与 Demo 布局 | 两个入口的内容高度和按钮位置不统一 | 等高卡片、精简说明、按钮占满卡片宽度并放在底部 | 排版优化 |
| Demo 标题与按钮 | `Demo project` / `Load demo project` | `Explore demo` / `Explore demo` | 文案优化 |
| Demo 上传说明 | 未直接强调用户无需上传文件 | 明确显示 `No file upload needed.` | 信息补充 |
| Demo 文件详情 | 直接出现在卡片主说明中 | 收入默认折叠的 `What's included` | 信息收纳 |
| 五步流程 | 页面导航和局部提示体现流程，首页没有统一步骤展示 | 欢迎页和项目首页展示五步流程 | 新增组件 |
| 流程状态 | 没有统一的首页步骤状态 | 显示 `Next`、`Complete`、`Upcoming`、`If needed` | 新增状态展示 |
| 下一步指引 | 未准备好审核时，在 `Readiness` 下提供局部链接 | 在首页顶部显示独立的动态 `Next step` 区域 | 功能扩展 |
| 主要操作 | 审核启动按钮主要位于 `Readiness` 下方 | 当前步骤入口使用主按钮，重复审核使用次要按钮 | 操作层级优化 |
| 审核统计 | 四个 `st.metric` 指标和常规 `View` 按钮 | 保留四个统计，采用较轻的文字、数字和 `View` 操作 | 排版优化 |
| `Readiness` | 六项检查一直展开 | 未通过时展开，全部通过时默认收起 | 展示逻辑优化 |
| 审核轮次 | 首页直接展示全部轮次表格 | 突出最近轮次，完整表格收入折叠区域 | 信息收纳 |
| 第二项目创建 | 底层能保存多个项目，但已有项目时缺少持续可用的新建入口 | 侧边栏始终有 `New project`，新建后仍可切回原项目 | 功能补齐 |
| 侧边栏顺序 | 框架生成的页面导航在上，项目及身份控件在下 | 项目操作在上，随后是页面导航、身份信息和数据说明 | 导航优化 |
| 引擎说明 | 版本信息直接显示 | 收入 `Engine details`，数据处理说明仍在外部显示 | 信息收纳 |
| 窄屏布局 | 主要依赖原有列布局，未针对本次首页内容统一处理 | 流程允许换行，入口卡片及主要列明确启用换行，关键按钮允许文字换行 | 自适应优化 |
| 审查与导出 | 原有审核、复检、签署、Excel/PDF 导出 | 保留原有业务逻辑和导出流程 | 保持不变 |

## 3 欢迎页的变化

### 3.1 进入欢迎页的条件

最初首页通过 `if not project:` 决定是否显示欢迎内容。公共组件还会在未选择项目时自动选择一个已有项目，因此，只要数据库中已经有项目，返回 `Overview` 通常就会进入项目概览，新建表单也就不会出现。

当前增加了独立的 `_show_project_start` 会话状态。欢迎页在“没有项目”或“用户主动点击了 `New project`”时显示。打开新建页面并不清空当前项目编号，也不删除原项目。

如果已有当前项目，欢迎页会显示 `Back to current project`。用户可以直接返回，也可以从侧边栏的 `Project` 选择框打开已有项目。

### 3.2 介绍文案

最初的介绍是一段较长的说明：

> Evidence-based tender compliance review: check every tender requirement against the bid, trace the evidence, remediate issues and sign off a compliance matrix.

当前分为重点句和补充说明：

> Evidence-based bid compliance review.

> Check tender requirements against the bid, trace findings to source pages, and resolve issues before sign-off.

变化的重点是让用户先识别产品用途，再了解证据如何回到来源页，以及审查结果如何进入问题处理和签署。

### 3.3 New project 卡片

最初卡片标题是 `New project`，实际提交按钮是 `Create project`。这一区别仍然保留；侧边栏新增的 `New project` 按钮负责打开入口，卡片内的 `Create project` 才会创建项目。

当前卡片补充了简短说明 `Start with your own tender and bid documents.`，同时调整了布局：

- 外层卡片保留边框，内部 `st.form` 使用 `border=False`，避免重复套框。
- 卡片与表单使用可伸展高度，利用原生空间组件将提交按钮放到底部。
- `Create project` 使用主按钮、添加图标，并占满可用宽度。
- 空项目名仍会触发 `Enter a project name.`，提示位置调整到按钮上方。
- 成功创建后仍然选中新项目并进入 `Documents`，没有改变这条既有业务路径。

### 3.4 Explore demo 卡片

原来的标题 `Demo project` 和按钮 `Load demo project` 统一改为 `Explore demo`。卡片主说明简化为：

> Try a prepared sample project. No file upload needed.

详细文件说明移入默认折叠的 `What's included`，说明示例包含虚构的英文排水工程招标文件、Addendum 1、Bid v1，以及供后续复检使用的 Bid v2。详情区域位于按钮上方，按钮保持在卡片底部。

Demo 仍调用原有 `demo.create_demo_project()`。它会生成示例项目、解析初始文件并提取需求。`No file upload needed.` 的含义是用户无需事先提供文件；并不表示需求已经全部人工确认、审核已经完成或项目已经签署。Demo 首次进入项目后，通常会被引导到 `Requirements` 处理补遗和确认清单。

### 3.5 欢迎页结构示意

最初结构：

```text
TenderCheck
完整介绍段落

New project                 Demo project
  内部有边框的表单            直接展示示例文件介绍
  Project name              Load demo project
  Create project
```

当前结构：

```text
TenderCheck
Evidence-based bid compliance review.
简洁说明证据追溯和问题处理
Back to current project     仅已有项目时显示

New project                 Explore demo
  简短说明                    No file upload needed.
  无边框的项目表单             What's included 折叠区域
  Create project            Explore demo
  底部整行按钮                 底部整行按钮

Review workflow
Documents → Requirements → Review → Remediation → Sign-off
```

## 4 项目首页的信息顺序变化

### 4.1 最初的阅读顺序

最初进入项目后，用户先看到项目名称、投标版本及轮次信息，然后是运行进度、审核统计、`Readiness` 与角色待办，再往下是完整轮次表格和项目数据管理。

“下一步”并不是固定存在的主要区域。只有未满足审核条件时，`Readiness` 内才会提供一个前置步骤链接。

### 4.2 当前的阅读顺序

当前将具体行动放在更靠前的位置：

```text
项目名称
投标版本和轮次信息 使用较轻的 caption

Next step · 当前阶段
  当前状态说明
  当前阶段的主操作按钮

Review workflow
  五个步骤及状态标记

任务进度与错误提示

Review summary              有已完成审核轮次时显示
  四项核心统计与 View 入口
  系统检查和人工决策进度

Readiness                   To do · Reviewer 或 Manager
  根据准备状态展开或收起       保留角色相关待办

Review rounds
  最近轮次摘要
  Open latest results
  All review rounds 折叠表格

Project data 折叠区域
```

这项调整的目的，是让用户进入项目后先确认当前需要完成的任务，再查看统计、准备条件和历史记录。

## 5 Next step 从局部提示扩展为状态驱动入口

### 5.1 最初的提示逻辑

最初只有在 `ready` 为假时显示 `Next step: open Documents` 或 `Next step: open Requirements`。如果缺失检查中包含需求或补遗检查，逻辑就倾向于进入 `Requirements`。因此，当文档和需求条件同时缺失时，提示可能先指向需求页。

当准备条件已经满足、审核进行中、需要人工核查、需要整改或已经签署时，没有统一的首页 `Next step` 区域，用户主要依靠页面导航和角色待办找到入口。

### 5.2 当前的判断规则

当前由 `ui.project_next_step()` 读取已有准备检查、任务、轮次、投标版本和整改状态。下面的表格按照实际判断优先级排列；如果同时符合多个条件，靠前的条件先决定入口。

| 优先级 | 当前数据状态 | 显示阶段 | 对应操作或页面 |
| --- | --- | --- | --- |
| 1 | 自动审核任务正在运行 | `Review` | 留在 `Overview` 查看进度；审核启动按钮禁用 |
| 2 | 所选文档缺失、尚未解析、解析失败或页数检查未通过 | `Documents` | `Open Documents` |
| 3 | 需求提取任务正在运行 | `Requirements` | `Open Requirements` 查看状态 |
| 4 | 补遗尚未处理或需求清单尚未确认 | `Requirements` | `Open Requirements` |
| 5 | 准备检查全部通过，尚无已完成审核轮次 | `Review` | `Start review` |
| 6 | 当前投标版本不同于最近已完成轮次，且有未关闭整改项 | `Remediation` | `Open Remediation`，准备复检和比较结果 |
| 7 | 当前投标版本不同于最近已完成轮次，且没有未关闭整改项 | `Review` | `Start review of new bid` |
| 8 | 当前版本对应的最近轮次已经签署 | `Sign-off` | `Open Report & Sign-off`，查看和导出 |
| 9 | 当前轮次仍有尚未记录人工决定的适用条款 | `Review` | `Open Review Workspace` |
| 10 | 存在等待复检确认的整改项 | `Review` | `Open Review Workspace`，核查新证据并确认 |
| 11 | 存在其他未关闭整改项 | `Remediation` | `Open Remediation` |
| 12 | 仍有其他签署阻碍条件 | `Review` | `Open Review Workspace` |
| 13 | 上述条件均不成立，人工决定已完成 | `Sign-off` | `Open Report & Sign-off` |

这也解释了两个容易混淆的情况：上传新 Bid 后，即使旧轮次已经签署，也会优先提示对新版本审核或复检；存在未关闭整改项时，首页会优先引导整改，但在当前轮次未签署时仍提供查看签署要求和已披露风险的次要入口。

### 5.3 主操作与次要操作

`Next step` 中的页面入口由文字链接改为主按钮。首页内直接启动审核的按钮同样可以成为主操作。位于准备检查附近的重新审核操作使用次要按钮，统计中的 `View` 使用更轻的三级按钮。

这些调整改变的是操作的显著程度，没有改变后台审核启动条件、任务提交方式或页面业务逻辑。审核运行期间仍会禁用启动按钮。

## 6 五步流程与完成状态

### 6.1 新增的流程展示

欢迎页和项目首页现在都展示以下五步：

| 步骤 | 英文说明 | 用户需要理解的任务 |
| --- | --- | --- |
| `Documents` | `Upload tender and bid` | 上传并选择参与审查的文档 |
| `Requirements` | `Confirm what to check` | 提取、核对并确认审查需求 |
| `Review` | `Trace and verify evidence` | 检查结果、追溯证据并记录人工决定 |
| `Remediation` | `Resolve and re-inspect if needed` | 在需要时整改、上传新版本并复检 |
| `Sign-off` | `Approve and export` | 完成签署并导出交付材料 |

欢迎页只说明流程；进入项目后才根据实际状态展示完成情况和下一步。

### 6.2 状态标记的含义

| 标记 | 展示含义 | 需要注意 |
| --- | --- | --- |
| `Next` | 当前优先处理的步骤 | 由当前数据和任务状态决定 |
| `Complete` | 该步骤符合当前完成判断 | 不等于所有投标条款均合规 |
| `Upcoming` | 尚未被标记为完成，也不是当前优先步骤 | 不构成必须严格按顺序执行的限制 |
| `If needed` | 整改属于按需进行的阶段 | 不表示系统自动判定所有问题已经解决 |

当前实现中，`Complete` 的显示优先于 `Next`。例如已签署轮次的下一步仍是进入 `Sign-off` 页面导出，此时流程中的 `Sign-off` 可以显示 `Complete`，而顶部 `Next step` 继续给出导出入口。

### 6.3 完成状态如何确定

- `Documents`：现有准备检查中的文档相关项目全部通过，包括招标和投标文档、解析状态及当前包的页数限制。
- `Requirements`：全部准备检查通过，包括补遗决定和需求清单确认。
- `Review`：准备检查通过、最近已完成轮次对应当前所选 Bid、没有正在运行的审核任务，并且该轮次没有现有服务层定义的签署阻碍条件。
- `Remediation`：在 `Review` 达到上述完成条件后，项目确实存在整改记录，且这些记录全部为 `Closed`。
- `Sign-off`：在准备检查、Bid 版本和任务状态检查通过后，当前轮次已有签署记录。

不存在整改记录时，系统不会仅因流程已经走到后面，就把 `Remediation` 标为完成。上传了尚未审核的新 Bid 后，旧轮次的审核和签署也不会继续作为新版本的完成标记。

本次没有增加“所有不合规问题必须全部消除才能签署”的新规则。原有规则允许在人工决定完成、签署阻碍解除后，将仍然存在的风险披露在报告中。因此，`Review` 完成、`Sign-off` 完成与“投标全部合规”是不同含义。

## 7 审核统计 准备条件与轮次历史

### 7.1 四项统计保留了什么

统计仍使用原来的 `service.round_summary()`，以下四个名称和计算口径没有变化：

| 名称 | 统计内容 |
| --- | --- |
| `Awaiting review` | 适用条款中尚未记录人工决定的数量 |
| `Non-compliant` | 适用条款中自动判定不合规的数量 |
| `Insufficient evidence` | 适用条款中证据不足的数量 |
| `In remediation` | 适用条款中人工状态为待整改或待复检的数量 |

最初使用带背景和边框的 `st.metric` 展示。当前在 `Review summary` 下使用辅助文字、较轻的数字标题和三级 `View` 操作，以减少统计区对主任务的视觉干扰。每个 `View` 仍进入对应筛选条件的 `Review Workspace`。两条系统检查与人工决定进度仍保留。

### 7.2 Readiness 展开规则

六项检查及其服务层判断没有变化。最初检查清单一直显示；当前清单位于折叠区域中：

- 全部通过时，显示 `Readiness · Ready`，默认收起。
- 尚未通过时，标题显示剩余检查数量，默认展开。

用户仍能查看每项检查和附加说明。收起只影响首页的信息密度，不会跳过准备检查，也不会放宽审核启动条件。

### 7.3 最近轮次与完整历史

最初 `Review rounds` 直接显示全部轮次表格。当前先展示最近一轮的轮次号、对应 Bid、状态、启动时间和签署摘要，并提供 `Open latest results`。

该按钮在最近轮次尚未完成时禁用，避免把运行中的轮次当作可供完整核查的结果。完整表格保存在默认收起的 `All review rounds (N)` 中，仍包含原有的轮次、Bid、启动时间、状态、签署和规则版本字段。没有删除历史轮次或原有结果。

角色待办 `To do · Reviewer` / `To do · Manager` 与 `Project data` 管理区域仍然保留。原有删除项目的确认框和删除操作也没有移除或自动执行。

## 8 侧边栏与多项目使用方式

### 8.1 原来的侧边栏结构

```text
框架默认页面导航
  TenderCheck
    Overview
  Review
    Documents
    Requirements
    Review Workspace
    Remediation
  Delivery
    Report & Sign-off

Project
Role
Your name
分隔线
数据处理说明
Engine 和 Rules 版本
```

原侧边栏已经可以切换存在的项目，也已经提供角色选择、姓名输入和数据处理说明。本次没有将这些原有能力作为全新功能重新添加。

### 8.2 当前的侧边栏结构

```text
Projects
  New project
  Project

Workspace
  Overview
Review
  Documents
  Requirements
  Review Workspace
  Remediation
Delivery
  Report & Sign-off

Reviewer details
  Role
  Your name

Data processing
  本地处理说明或外部服务提示
  Engine details 折叠区域
```

页面仍然是原来的六个页面；第一组导航标签由 `TenderCheck` 调整为 `Workspace`。`New project` 始终使用次要按钮样式，避免抢占当前项目首页主操作的视觉层级。

### 8.3 创建第二个项目的实际差异

最初的存储层本身能保存多个项目，但常用界面缺少持续可达的新建入口。当前补齐了这条路径：

1. 在任意页面点击侧边栏 `New project`。
2. 当前项目保留在存储中，并保留当前选择上下文；主区域打开欢迎页。
3. 输入新名称并点击 `Create project`，创建、选中新项目并进入 `Documents`。
4. 从 `Project` 下拉框选择原项目，即可返回原项目的数据和结果。

在新建页面尚未提交时，`Project` 选择框显示 `Open an existing project`，便于明确回到已有项目。点击 `Back to current project` 也会退出新建界面。通过页面导航直接离开新建界面时，相应显示状态会清除，之后回到 `Overview` 不会一直停留在新建卡片上。

### 8.4 身份与数据说明

`Role` 和 `Your name` 被归入 `Reviewer details`，仍参与原有操作记录和角色控制。此次没有新增账号系统、登录验证或权限体系。

本地处理说明和外部服务提示仍在 `Data processing` 下直接展示。只有引擎名称和规则版本被收入 `Engine details`。配置了 `TENDERCHECK_LLM_PROVIDER` 时，外部发送文档文字的提示仍是可见的警告；这里只是保留已有配置的说明，并没有新接入任何 LLM 服务。

## 9 风格与自适应布局

### 9.1 保留的风格

应用仍使用 Python 和 Streamlit，保持宽屏布局、浅灰背景、白色内容区域和深蓝主色。`.streamlit/config.toml` 没有在本次改版中修改，也没有引入新的前端框架、字体资源或第三方组件。

原有公共 CSS 仍保留。新增排版主要使用 Streamlit 的容器、列、按钮、标签和折叠区域实现，没有为首页新增一套依赖内部 DOM 选择器的 CSS。

### 9.2 当前做了哪些窄屏适配

- 五步流程改为允许换行的横向容器，单个步骤使用紧凑的内容宽度；空间不足时可以换到下一行。
- 欢迎页双卡片、统计列、进度列及 `Readiness` 与角色待办的双列布局明确允许换行。
- 新建、Demo、审核和 `Next step` 等关键按钮允许文字换行。
- 项目名称使用原生标题，版本和轮次信息使用较轻的辅助文字，沿用原生文本流处理长内容。

这些变化不代表初始版本完全没有自适应能力。原有 Streamlit 列布局本身就能在部分窄屏条件下堆叠；本次是在此基础上，对新增流程和关键操作做了更明确的处理。

侧边栏中的长项目名仍由原生 `selectbox` 展示，不能据此承诺其选中项在所有宽度下都会完整换行。完整项目名可以在项目首页标题中查看。

### 9.3 尚未完成的视觉实测

当前环境没有可用浏览器，因此本轮没有完成真实浏览器中的宽窄屏截图核验。自动化测试已经验证页面能够运行、元素和入口存在以及相关状态行为，但不会验证实际像素位置、滚动距离、字体渲染或按钮底边是否在所有尺寸下完全对齐。

本文所述等高、底部布局和换行均有代码实现；具体视觉效果应在实际运行页面中复核。

## 10 代码结构与维护位置

| 文件 | 本次变化 | 后续维护用途 |
| --- | --- | --- |
| `src/app/main.py` | 从公共 `NAVIGATION` 注册页面，隐藏框架默认导航，并向公共侧边栏传入当前页面信息 | 入口初始化和路由运行 |
| `src/app/views/overview.py` | 欢迎页、项目首页结构、主操作、统计、准备条件和历史展示 | 修改首页文字与排版 |
| `src/app/tcui/ui.py` | 导航配置、侧边栏、新建页显示状态、流程组件、下一步和完成状态判断 | 修改共享导航和首页引导 |
| `tests/test_home_navigation.py` | 新增七项首页与导航回归测试 | 验证创建、导航、状态展示和折叠行为 |

`main.py` 仍使用 `st.navigation()` 和 `st.Page()` 注册页面。当前将默认导航设为隐藏，再由公共侧边栏使用 `st.page_link()` 展示同一组页面，从而实现项目操作位于导航上方的顺序。没有删除页面注册，也没有改变各页面对应的文件路径。

`NAVIGATION` 集中保存分组、页面名称与图标，同时供注册和侧边栏使用，减少两处配置不一致的可能。

主要代码定位如下，行号对应本文生成时的版本：

- `main.py` 第 27 行附近：注册导航并交给公共侧边栏展示。
- `ui.py` 第 23 行附近：`NAVIGATION`。
- `ui.py` 第 109 行附近：`sidebar()`。
- `ui.py` 第 166 行附近：`workflow_steps()`。
- `ui.py` 第 190 行附近：`workflow_completed_steps()`。
- `ui.py` 第 213 行附近：`project_next_step()`。
- `overview.py` 第 8 行附近：欢迎页显示条件。
- `overview.py` 第 74 行附近：`Next step` 主操作区。
- `overview.py` 第 104、124、168 行附近：统计、准备条件和轮次历史。

## 11 保留的业务能力与数据

本次调整保留了以下内容：

- 入口仍为 `src/app/main.py`，使用现有 `.venv` 启动。
- `data` 目录、SQLite 数据库、已有项目文件、审核结果和操作记录保留。
- 文档上传和解析、需求提取与确认、证据检索及规则判定没有改写。
- 整改建议、修订 Bid 上传、复检和轮次比较仍沿用原有流程。
- `Report & Sign-off` 中的签署与 Excel/PDF 导出逻辑没有修改。
- `Documents`、`Requirements`、`Review Workspace`、`Remediation`、`Report & Sign-off` 的主体页面文件没有在本次改版中编辑；它们共享的侧边栏会显示新版导航。

特别需要区分：可追溯证据核查、Demo 生成、后台审核任务和报表导出在原版本中就存在。本次让这些能力更容易理解和到达，并补充了原来缺失的新建入口和统一状态引导。

## 12 验证结果与验证边界

### 12.1 已实际运行的测试

最后一次功能改版验证中，七项首页与导航测试及两项原有审核测试全部通过，共九项。测试使用隔离的临时数据目录，不以用户的已有项目作为测试夹具。

| 测试组 | 数量 | 覆盖内容 |
| --- | --- | --- |
| 首页与导航 | 7 | 欢迎页与 Demo、第二项目及原项目保留、完整阶段引导、文档优先级、首页审核启动、六页导航和身份保留、准备条件与历史折叠及外部服务提示 |
| 原有审核流程 | 2 | 规则修正，以及审核、人工处理、整改复检、签署和 Excel/PDF 导出的完整回归 |

此外，修改文件通过 Python 语法编译检查，Git 差异空白检查也已通过。本次生成对比文档时没有再次修改应用功能，也没有重复运行已通过的整套测试。

需要复现测试时，在 `SystemCode` 下执行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_home_navigation.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_review_app.py -v
```

当前虚拟环境没有安装 `pytest`，所以实际采用现有测试本身支持的 `unittest`。为本次验证没有新增测试依赖或替换虚拟环境。

### 12.2 已验证与未实测的区别

| 项目 | 证据或状态 |
| --- | --- |
| 新建第二个项目不会删除原项目 | 已通过测试验证原项目记录和示例保留文件仍存在 |
| 原项目切换与返回 | 已通过测试 |
| 六个页面及常驻新建入口 | 已通过无浏览器的 AppTest 测试 |
| 新版 Bid 不沿用旧版本的审核与签署完成状态 | 已通过状态回归测试 |
| 准备条件折叠、历史折叠及数据提示 | 已通过 AppTest 测试 |
| 审核规则、复检、签署与导出回归 | 已通过原有测试 |
| 卡片精确高度、具体窗口下的文字换行和视觉对齐 | 有原生布局实现，尚未进行浏览器截图实测 |

## 13 查看当前效果的方法

在 `SystemCode` 目录使用现有虚拟环境启动：

```powershell
.\.venv\Scripts\python.exe -m streamlit run src/app/main.py
```

打开 `http://localhost:8501`。如果应用已经运行，可以刷新页面查看；必要时停止旧进程并使用上述命令重新启动。

建议按以下顺序查看：

1. 点击侧边栏 `New project`，查看介绍、两张入口卡片和五步流程。
2. 展开 `What's included`，确认文件详情被收纳且 Demo 按钮仍可访问。
3. 如需验证多项目，创建一个明确命名的测试项目，再通过 `Project` 切回原项目。该操作会实际新增项目。
4. 打开已有项目的 `Overview`，观察顶部 `Next step` 和对应主按钮。
5. 在不同准备状态的项目中查看 `Readiness` 的展开状态。
6. 在已有审核轮次的项目中查看 `Review summary`、最近轮次和 `All review rounds (N)`。
7. 缩小浏览器窗口，重点检查五步流程、双卡片、长项目名和长按钮文字的显示。

`Explore demo` 会实际创建一个新的示例项目，并不是只打开静态演示页。对仅需阅读的演示场合，可以直接打开已有 Demo 项目，避免重复生成。

## 14 备份阶段与查证入口

| 备份目录 | 保存时机 | 可以用于核对的内容 |
| --- | --- | --- |
| `backups/home-navigation-20261020-223656/` | 第一轮首页和侧边栏修改前 | 最初的 `overview.py` 与 `ui.py` |
| `backups/next-step-20261020-225924/` | 调整 Next step 信息层级前 | 主操作区移动和按钮层级变化 |
| `backups/welcome-cards-20261020-230821/` | 调整入口卡片前 | 欢迎页等高和按钮布局变化 |
| `backups/layout-polish-20261020-231132/` | 最后一轮整体排版前 | 流程状态、侧边栏、信息收纳改版前的文件，以及未改版的原入口 |

以下链接可以直接打开相关文件：

- [最初首页](../backups/home-navigation-20261020-223656/src/app/views/overview.py)
- [最初公共界面组件](../backups/home-navigation-20261020-223656/src/app/tcui/ui.py)
- [最初入口代码](../backups/layout-polish-20261020-231132/src/app/main.py)
- [当前首页](../src/app/views/overview.py)
- [当前公共界面组件](../src/app/tcui/ui.py)
- [当前应用入口](../src/app/main.py)
- [首页与导航测试](../tests/test_home_navigation.py)
- [原有审核闭环测试](../tests/test_review_app.py)

本文件记录本轮完整改版的最终差异。后续继续调整页面时，可以保留本文作为阶段基准，再补充新的变更说明。
