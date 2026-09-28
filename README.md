# 错题本

家里自用的错题记录与分析工具。跑在自己电脑上，平板通过局域网访问，
无账号、无云端、数据只存在本机。

## 功能

| 功能 | 做了什么 | 在哪里 |
|---|---|---|
| 1. 分科记录错题 | 拍照（可裁剪）或打字录入，按科目和试卷批次归类 | 「错题」+「＋ 录错题」 |
| 2. 找学习漏洞 | 知识点覆盖率盘点、薄弱点排序、复习正确率趋势 | 「学情」「覆盖」 |
| 3. 讲解错题 | 本地多模态模型直接读题图讲解，指出错在哪一步，还能继续追问 | 「错题」详情页 → 🤖 |
| 4. 定期归纳 | 按已统计的数据生成 7 天复习计划与提分建议 | 「计划」 |
| 5. 自动归类 | AI 从本科目已有知识点里挑最匹配的，不用手工挂知识点 | 「错题」详情页 → 知识点 → 🤖 自动归类 |

## 界面使用要点

- **拍照后先裁剪**：拍照/选图后会自动弹出裁剪层。只留题目那一部分 ——
  拍整张卷子会把旁边的题一起送进模型，读出来的题干是串的。不想裁就点「全选」。
- **复习按钮可以改判**：误点了别的按钮，10 秒内改点正确的那个会**替换**刚才那一次
  （不新增记录，状态也跟着改回来）。超过 10 秒再点就是新的一次复习。
- **左栏可以收起**：点左上角 ☰ 收起错题列表，给做题腾出宽度；状态会记住。
- **字号可调**：顶栏 `A− / A+` 调全站字号；题目和讲解各自还有独立的字号档位
  （看题想要大字，读讲解要大段文字，两者需求不同）。
- **追问**：讲解下面就是聊天框，哪里没听懂直接问，对话会保存下来。

## 快速开始

```bash
# 1. 准备配置（.env 不进版本库）
cp .env.example .env
#    按需修改 DEEPSEEK_BASE_URL / DEEPSEEK_API_KEY

# 2. 启动
docker compose up -d

# 3. 看是否起来了
docker compose ps
curl http://localhost:8000/health
```

浏览器打开 `http://localhost:8000`，后台管理在 `http://localhost:8000/admin`。

平板访问用宿主机的局域网 IP（不是 localhost）：

```powershell
# 查当前局域网地址
.\scripts\find-url.ps1
```

## 配置项

全部通过 `.env` 或环境变量注入，见 `.env.example`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `DEEPSEEK_BASE_URL` | `http://fq-dgx-01:8888/v1` | OpenAI 兼容端点 |
| `DEEPSEEK_API_KEY` | `fq-dgx` | 本地端点用的 key |
| `DEEPSEEK_MODEL` | `deepseek-v4.1-flash` | 模型 id |
| `POSTGRES_*` | `mistakebook` | 数据库账号 |
| `API_PORT` | `8000` | 宿主机端口 |
| `DAILY_REVIEW_MINUTES` | `20` | 每日建议复习时长，喂给计划生成 |

## 模型注意事项（踩过的坑）

本机端点是 **sglang**，有两条与官方云 API 不同的行为，改代码前务必知道：

1. **图片块绝不能带 `detail` 字段。** 带 `"detail":"original"` / `"low"` / `"high"`
   一律 HTTP 400。而且报错文本是 pydantic 对 union 成员逐个失败的几十条
   validation error，**完全不会提到 `detail`**，极易误判成 `type` 写错。
   正确写法只有：
   ```json
   {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,..."}}
   ```
2. 图片放 `system` 消息里也能用（官方云 API 不允许），但代码统一放 `user`，
   以便日后切回云 API 不用改。

另外：`response_format={"type":"json_object"}` 可用；`GET /v1/models` 只有
`deepseek-v4.1-flash` 一个模型。

## 数据与备份

```
data/uploads/   题目原图（按年月日分目录）
data/books/     教材 PDF（本地私有，见下）
data/pgdata/    Postgres 数据目录
```

### 备份（推荐用 pg_dump，不要直接拷 pgdata）

```powershell
# 1) 导出数据库（文本格式，跨版本可恢复，能看清内容）
docker compose exec -T db pg_dump -U mistakebook -d mistakebook `
  | Out-File -Encoding utf8 "backup\mistakebook-$(Get-Date -Format yyyyMMdd).sql"

# 2) 拷走上传的题目图片
Copy-Item -Recurse data\uploads "backup\uploads-$(Get-Date -Format yyyyMMdd)"
```

> **不要**在 Postgres 运行时直接拷 `data/pgdata/` —— 可能拷到写了一半的文件，
> 恢复后库会损坏。要么用上面的 `pg_dump`，要么先 `docker compose stop db` 再拷目录。

### 恢复

```powershell
Get-Content "backup\mistakebook-20260927.sql" -Raw `
  | docker compose exec -T db psql -U mistakebook -d mistakebook
```

（Windows 上必须用 `Get-Content -Raw | ...` 管道，直接 `< file` 会让 PowerShell 报语法错误。）

**恢复一条被误删的错题**：删除是软删除（只写 `deleted_at`），
调用 `POST /api/mistakes/{id}/restore` 即可恢复，图片和复习历史都还在。

## 版权边界（重要）

- `data/books/` 里的教材 PDF 是**本地私有资源**：已在 `.gitignore` 中排除，
  **不要提交、不要分发、不要随应用打包**。
- 知识点骨架与教材页码属于课标级公共信息，可以入库。
- 自用（孩子学自己正在读的教材）没问题；若要分享或上架，**不得包含教材正文**。

## 安全说明

- 本应用**没有任何鉴权**，设计前提是只在家中局域网内使用。
- **不要把 8000 端口转发到公网**，不要做端口映射或内网穿透。
- 数据库端口没有发布到宿主机，只有 `api` 容器能连。

## 开发

```bash
# 后端测试（推荐用法：一条命令，自带 pytest，不需要手工装依赖）
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm test

# 前端模块一致性检查（无构建步骤，靠这个脚本兜底）
node scripts/check-imports.mjs

# 前端纯函数测试（标签文案、日期格式化等）
node scripts/test-ui-pure.mjs

# 前端视图接线测试（含最小 DOM 垫片 + 静态接线检查）
node scripts/test-views.mjs

# 详情页接线测试（复习改判 / 字号 / 追问 / 归类有没有真的接上接口）
node scripts/test-detail-wiring.mjs

# 左栏折叠后的布局计算（防止「折叠后右侧整屏空白」回归）
node scripts/test-collapse-layout.mjs

# 复习并发压测（真线程，验证 SRS 状态不丢更新）
python scripts/stress_review.py 20 5

# 完整验收（逐条跑 spec §10 的标准，需容器在跑）
docker compose -f docker-compose.yml -f docker-compose.test.yml \
  run --rm --entrypoint sh test -c "cd /app && PYTHONPATH=src python /app/scripts/acceptance.py"
```

> 原生 ES module 没有编译器兜底，**接线错误**（例如把空数组传给视图构造函数，
> 事后再想用赋值补上——赋值赋在返回对象上，是死代码）纯函数测试和 import
> 检查都发现不了。`test-views.mjs` 用一个最小 DOM 垫片把真实视图逻辑跑起来，
> 并附带静态检查防止这类写法回潮。

> `test-views.mjs` 与 `test-detail-wiring.mjs` 的垫片里，`getElementById` 必须
> **先扫元素树**再退回预登记表：视图在 render 时会新建带 id 的元素（追问的
> textarea、发送按钮等）并 append 进树里，只查预登记表会拿到一个游离的占位元素，
> 点它不触发任何逻辑 —— 症状是「测试点了按钮，但接口没被调用」，
> 很容易误判成业务代码有问题。

### 讲解的语气是硬需求，不是措辞偏好

用户明确提过：讲解要通俗易懂，**不能过于正式，否则孩子会反感**。
这不是「写得亲切一点更好」的建议，而是决定这个功能有没有人用的前提 ——
题讲得再对，孩子看到「由题意可知」「综上所述」的第一反应就是关掉页面。

因此 `app/src/mistakebook/services/prompts.py` 里每套会出文字的提示词
（讲解 / 追问 / 单题诊断 / 周计划）都单列了「说话方式」一节，硬性要求：

- 身份是**比孩子大几届的学长/姐姐**，不是老师、不是「学习诊断助手」；
- 用「你」直接说话，短句，说人话；
- **点名禁用**具体书面语：「由题意可知」「综上所述」「本题考查了」
  「值得注意的是」「该生」「有待加强」；
- **说事不说人**：讲哪一步、哪个方法错了，不评价孩子本人；
- 不灌鸡汤、不训人 —— 被评判或说教一次，他下次就不问了；
- 但也要防「亲切」滑向「油滑」：同龄口气一句就够，不要每段都插。

> 关键细节：只写「说人话」对模型无效，必须**把具体禁用词列出来**它才会避开。
> 另外 `error_analysis` 明确禁止罗列「可能是 A，也可能是 B」——猜错错因
> 会让孩子觉得「你根本没看懂我在写什么」，比不猜更糟；看不出来就如实说。

这些约束由 `app/tests/test_prompt_tone.py` 锁住，分两层：

1. **文本层**（离线必跑）：禁用词清单、口吻设定、人称、字段契约都还在；
2. **真实输出层**（`MB_RUN_MODEL_TESTS=1` 打开）：真跑一次讲解，
   扫描输出里有没有官腔、句长是否过长。文本层只能保证「我们要求了」，
   这一层才能发现「模型没照做」。

```bash
# 跑真实输出层的语气检查（需要模型服务）
docker compose -f docker-compose.yml -f docker-compose.test.yml run --rm \
  -e MB_RUN_MODEL_TESTS=1 test
```

### 左栏折叠为什么是「只留一列」

不能写成 `grid-template-columns: 0 1fr`。折叠时 `.list-pane` 已经是
`display: none`，会被移出网格，此时 `.detail-pane` 成了唯一的网格项，
按自动放置规则落进**第 1 条轨道**（宽度 0），右侧题目区被压成零宽 ——
现象就是「收起左栏后整屏空白」。留一列（`1fr`）不存在这个错位。

同理，`≤820px` 单栏档下不能再用 `body.list-collapsed .list-pane` 去覆盖
`body.show-detail .list-pane`：两者优先级完全相同而前者更靠后，
会让折叠过左栏的用户在手机上点进题目时被列表盖住。该档已隐藏折叠按钮，
折叠状态本就无效，直接不参与计算。

> 这两条都由 `scripts/test-collapse-layout.mjs` 锁住。它同时校验
> **CSS 文本**与**网格自动放置语义**，因此不只是「跑一遍不报错」——
> 把规则改回 `0 1fr` 会让它变红（已做过变异验证）。

### 复习改判（为什么 redo 由客户端声明）

复习提交带一个 `redo: true` 表示「替换上一条刚写下的记录」。**不**做成服务端
按时间自动判断，是因为那样会让程序化调用者（回归测试、`stress_review.py`、
验收脚本）连续提交的多次复习被误当成重复点击而静默丢弃 —— 实测踩到过，
连 SRS 的 1→2→4→8 推进测试都红了。自动推断「用户是不是手滑」本来就不可靠，
而界面**知道**自己处在哪个状态，让它显式说明最准确。

测试跑在**同一个数据库**上（库表由同一套迁移创建），因此夹具必须在 teardown 里
彻底清理：错题要物理删除（API 只能软删除），知识点要删掉。验证方式是跑测试前后
比对 `COUNT(*)` —— 数字必须完全一致。详见 `app/tests/conftest.py` 的说明。

> `app/Dockerfile` 是运行镜像，刻意不含 pytest（保持精简）。
> 测试依赖在 `Dockerfile.test` 里叠加，这样测试环境可复现，
> 不必每次 `exec` 进去 `pip install`。

改前端（`web/`）和后台模板不需要重建镜像——compose 用挂载覆盖了镜像内副本，
刷新浏览器即可。迁移文件（`app/migrations/`）同样挂了载，新增迁移后
**重启**容器即可生效，不必重建镜像：

```bash
docker compose restart api
```

改后端 Python 需要重建：

```bash
docker compose up -d --build api
```

## 架构

```
平板浏览器 ──HTTP/局域网──> api 容器(FastAPI :8000)
                             ├─ /            静态前端（原生 ES module，无构建）
                             ├─ /admin       服务端渲染后台（Jinja2）
                             ├─ /api/*       JSON 接口
                             └─ /uploads/*   题目原图
                                    │
                                    ├──> db 容器(Postgres 16)
                                    └──> fq-dgx-01:8888 (deepseek-v4.1-flash)
```

**一条关键设计边界**：所有统计数字由 SQL 算好，模型只负责把数字解释成人话。
绝不让模型自己去数错题——那是它最容易出错且我们无法察觉的用法。