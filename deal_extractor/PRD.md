# Deal Extractor - 产品需求文档 (PRD)

**版本**: 0.1.0
**最后更新**: 2025-01-31
**状态**: 基础框架完成

---

## 1. 项目概述

### 1.1 背景

Deal Extractor 是从 Telegram Deal Logging Bot 项目中提炼出的独立模块。原 bot 用于监控 Telegram 群消息，自动提取 VC deal 信息并记录到 Notion。在开发过程中，我们发现 deal 信息提取的核心逻辑可以独立出来，作为通用组件供其他项目使用。

### 1.2 目标用户

- 需要自动处理 deal 信息的开发者
- VC/投资机构的技术团队
- 构建类似 deal flow 管理工具的开发者

### 1.3 核心价值

1. **即插即用**: 简单的 API，快速集成
2. **灵活配置**: 支持多种 LLM 提供商和 deck 来源
3. **经验沉淀**: 整合了实际项目中验证过的最佳实践

---

## 2. 功能规格

### 2.1 核心功能

#### 2.1.1 链接检测 (links/)

**已完成** ✅

| 功能 | 描述 | 状态 |
|------|------|------|
| URL 提取 | 从文本中提取所有 URL | ✅ |
| URL 分类 | 识别 URL 类型（DocSend, PDF, etc.） | ✅ |
| Deck 识别 | 判断是否为 pitch deck 链接 | ✅ |
| 优先级排序 | 按重要性排序链接 | ✅ |
| 重定向解析 | 解析包装/重定向 URL | ✅ |

**支持的链接类型**:
- DocSend (优先级 100)
- Papermark (优先级 90)
- Pitch.com (优先级 88)
- PDF 直链 (优先级 85)
- Google Drive (优先级 70)
- Dropbox (优先级 60)
- Notion (优先级 50)
- Loom (优先级 40)
- YouTube (优先级 35)

#### 2.1.2 内容提取 (extractors/)

**部分完成** 🔄

| 提取器 | 描述 | 状态 |
|--------|------|------|
| DocSendExtractor | API + 浏览器降级 | ✅ |
| PDFExtractor | pypdf + 可选 OCR | ✅ |
| GoogleSlidesExtractor | 导出 PDF 后提取 | ✅ |
| PapermarkExtractor | Papermark 平台 | ❌ 待实现 |
| PitchExtractor | Pitch.com 平台 | ❌ 待实现 |
| LoomExtractor | Loom 视频 | ❌ 待实现 |

#### 2.1.3 LLM 分析 (llm/)

**已完成** ✅

| 功能 | 描述 | 状态 |
|------|------|------|
| Router Agent | 快速判断是否为 deal | ✅ |
| Extractor Agent | 深度提取 deal 信息 | ✅ |
| 多 LLM 支持 | 支持 OpenAI 兼容 API | ✅ |
| JSON 解析 | 从 LLM 输出提取 JSON | ✅ |
| 多 Deal 支持 | 一条消息多个 deal | ✅ |
| External Source | 每个 deal 独立提取来源 | ✅ |

### 2.2 数据模型

#### Deal (已完成 ✅)

```python
@dataclass
class Deal:
    company_name: str              # 必需
    tags: list[str]                # 从 AVAILABLE_TAGS
    intro: str                     # < 140 字符
    detailed_content: str          # Markdown 格式
    deck_url: Optional[str]
    external_source: Optional[str]
```

#### ExtractionResult (已完成 ✅)

```python
@dataclass
class ExtractionResult:
    success: bool
    deals: list[Deal]
    error: Optional[str]
    skipped_reason: Optional[str]
    router_tokens: int
    extractor_tokens: int
    total_tokens: int
    decks_fetched: int
```

### 2.3 可用标签

```python
AVAILABLE_TAGS = [
    "DeFi", "AI", "Gaming", "Infrastructure", "SocialFi",
    "NFT", "DAO", "L1/L2", "Privacy", "Data",
    "Payments", "Enterprise", "Consumer", "Developer Tools", "Research",
]
```

---

## 3. 技术规格

### 3.1 依赖

**核心依赖**:
- `openai>=1.0.0` - LLM API 客户端
- `aiohttp>=3.8.0` - 异步 HTTP
- `httpx>=0.24.0` - HTTP 客户端
- `pypdf>=3.0.0` - PDF 文本提取
- `Pillow>=9.0.0` - 图像处理

**可选依赖**:
- `playwright>=1.40.0` - DocSend 浏览器提取
- `playwright-stealth>=1.0.0` - 反检测
- `img2pdf>=0.4.0` - 图片转 PDF

### 3.2 Python 版本

- 最低要求: Python 3.10+
- 使用了 `list[str]` 等类型提示语法

### 3.3 异步设计

所有网络操作都是异步的:
- `DealExtractor.extract()` - async
- `DocSendExtractor.extract()` - async
- `GoogleSlidesExtractor.extract()` - async
- `LLMExtractor.extract()` - async

PDFExtractor 是同步的（因为 subprocess 调用）。

---

## 4. 实施路线图

### Phase 1: 基础框架 ✅ (已完成)

- [x] 项目结构
- [x] 数据模型
- [x] 链接检测
- [x] DocSend 提取器
- [x] PDF 提取器
- [x] Google Slides 提取器
- [x] LLM 两阶段提取
- [x] 主入口类
- [x] 基础文档

### Phase 2: 测试与验证 🔄 (进行中)

- [ ] 单元测试
  - [ ] links/detector.py 测试
  - [ ] models/types.py 测试
  - [ ] extractors/ 各模块测试
  - [ ] llm/extractor.py 测试
- [ ] 集成测试
  - [ ] 端到端流程测试
  - [ ] 真实 API 测试
- [ ] 测试覆盖率 > 80%

### Phase 3: 功能完善 ❌ (待开始)

- [ ] Papermark 提取器
- [ ] Pitch.com 提取器
- [ ] Loom 提取器（视频转文字）
- [ ] 缓存机制（避免重复提取）
- [ ] 重试机制完善
- [ ] 更好的错误处理

### Phase 4: 发布准备 ❌ (待开始)

- [ ] pyproject.toml 配置
- [ ] PyPI 发布
- [ ] GitHub Actions CI/CD
- [ ] 完整 API 文档
- [ ] 示例项目

---

## 5. 已知问题与限制

### 5.1 DocSend

| 问题 | 描述 | 解决方案 |
|------|------|----------|
| CAPTCHA | Arkose Labs CAPTCHA 阻止自动化 | 使用 docsend2pdf.com API 或手动 cookie |
| 密码保护 | 部分文档需要密码 | 支持传入 password 参数 |
| API 限流 | docsend2pdf.com 有速率限制 | 内置限流（4 req/s） |

### 5.2 Google

| 问题 | 描述 | 解决方案 |
|------|------|----------|
| 权限 | 非公开文档无法访问 | 返回明确错误信息 |
| 登录 | 需要登录的文档 | 目前不支持 |

### 5.3 LLM

| 问题 | 描述 | 解决方案 |
|------|------|----------|
| JSON 解析 | LLM 输出可能不是标准 JSON | 多重解析策略 |
| 输出截断 | 长内容可能被截断 | 增加 max_tokens |
| 幻觉 | LLM 可能编造信息 | Prompt 中明确禁止 |

---

## 6. 接口设计

### 6.1 主入口

```python
from deal_extractor import DealExtractor

extractor = DealExtractor(
    llm_api_key="sk-xxx",
    llm_model="kimi-k2.5",
    llm_base_url="https://api.moonshot.cn/v1",
    docsend_email="xxx@example.com",
    docsend_password=None,
    pdf2llm_path=None,
    temp_dir=Path("./temp"),
)

result = await extractor.extract(
    text="消息内容...",
    sender="发送者名称",
    pdf_content=None,  # 可选：预提取的 PDF 内容
)
```

### 6.2 返回值示例

**成功提取 deal**:
```python
ExtractionResult(
    success=True,
    deals=[
        Deal(
            company_name="Acme Corp",
            tags=["AI", "Infrastructure"],
            intro="AI-powered developer tools for faster coding",
            detailed_content="# Acme Corp\n\n## Overview\n...",
            deck_url="https://docsend.com/view/xxx",
            external_source="John from VC Fund",
        )
    ],
    error=None,
    skipped_reason=None,
    router_tokens=150,
    extractor_tokens=800,
    total_tokens=950,
    decks_fetched=1,
)
```

**不是 deal**:
```python
ExtractionResult(
    success=True,
    deals=[],
    error=None,
    skipped_reason="Message is a news article, not a deal",
    router_tokens=120,
    extractor_tokens=0,
    total_tokens=120,
    decks_fetched=0,
)
```

**提取失败**:
```python
ExtractionResult(
    success=False,
    deals=[],
    error="DocSend extraction failed: CAPTCHA required",
    skipped_reason=None,
    router_tokens=0,
    extractor_tokens=0,
    total_tokens=0,
    decks_fetched=0,
)
```

---

## 7. 配置选项

### 7.1 LLM 配置

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| llm_api_key | str | 必需 | API Key |
| llm_model | str | "kimi-k2.5" | 模型名称 |
| llm_base_url | str | Moonshot | API 地址 |

### 7.2 DocSend 配置

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| docsend_email | str | None | 认证邮箱 |
| docsend_password | str | None | 文档密码 |

### 7.3 其他配置

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| pdf2llm_path | Path | None | OCR 工具路径 |
| temp_dir | Path | "./temp/deal_extractor" | 临时文件目录 |

---

## 8. 扩展指南

### 8.1 添加新的提取器

1. 在 `extractors/` 创建新文件
2. 继承 `BaseExtractor`
3. 实现 `extract()` 方法
4. 在 `extractors/__init__.py` 导出
5. 在 `DealExtractor._fetch_deck()` 添加分支

```python
# extractors/papermark.py
from .base import BaseExtractor
from ..models.types import FetchedDeck

class PapermarkExtractor(BaseExtractor):
    async def extract(self, url: str, password: str = None) -> FetchedDeck:
        # 实现提取逻辑
        ...
```

### 8.2 添加新的链接类型

1. 在 `LinkType` 枚举添加类型
2. 在 `DOMAIN_PATTERNS` 添加域名模式
3. 在 `PRIORITY_MAP` 设置优先级
4. 如果是 deck 类型，更新 `is_deck_link()`

### 8.3 修改 Prompt

编辑 `llm/prompts.py`:
- `ROUTER_PROMPT`: 修改判断逻辑
- `EXTRACTOR_PROMPT`: 修改提取字段
- `AVAILABLE_TAGS`: 添加/修改标签

---

## 9. 性能考虑

### 9.1 Token 优化

- Router 使用小 context (~500 tokens)
- Extractor 只在必要时调用
- Deck 内容截断到 6000 字符

### 9.2 并发安全

- 每次提取使用唯一 ID 目录
- URL hash 生成唯一文件名
- 避免竞态条件

### 9.3 网络优化

- 并行获取多个 deck
- 合理的超时设置
- 重试机制（待完善）

---

## 10. 安全考虑

### 10.1 API Key 保护

- 不在日志中打印完整 key
- 建议使用环境变量

### 10.2 临时文件

- 使用独立目录
- 定期清理机制（待实现）

### 10.3 输入验证

- URL 格式验证
- 防止路径遍历

---

## 附录 A: 与 Bot 的对比

| 方面 | 原 Bot | Deal Extractor |
|------|--------|----------------|
| Telegram 依赖 | 有 | 无 |
| LLM Provider | 硬编码 Kimi | 可配置 |
| 代码组织 | 分散 | 模块化 |
| 文档 | 最小 | 完整 |
| 可测试性 | 低 | 高 |
| 可复用性 | 低 | 高 |

## 附录 B: 参考资源

- 原 Bot 代码: `../bot/`
- pdf2llm 工具: `../PDF_Extractor 2/pdf2llm.py`
- DocSend Cookie 设置: `../setup_docsend_cookies.py`

## 附录 C: 变更日志

### v0.1.0 (2025-01-31)

- 初始版本
- 基础框架完成
- 支持 DocSend, PDF, Google Slides
- 两阶段 LLM 架构
