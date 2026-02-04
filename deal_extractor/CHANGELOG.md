# Changelog

All notable changes to Deal Extractor will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [Unreleased]

### Planned
- Papermark 提取器
- Pitch.com 提取器
- 单元测试
- 集成测试
- CI/CD 配置

---

## [0.1.0] - 2025-01-31

### Added

#### 核心框架
- 项目初始化，建立模块化结构
- 数据模型定义 (`models/types.py`)
  - `Deal` - 交易信息
  - `ExtractionResult` - 提取结果
  - `FetchedDeck` - 获取的 deck 内容
  - `RouterDecision` - Router 决策
  - `PDFExtractionResult` - PDF 提取结果
  - `DocSendExtractionResult` - DocSend 提取结果

#### 链接检测 (`links/`)
- `LinkDetector` 类实现
- 支持 14 种链接类型分类
- 优先级排序机制
- 重定向 URL 自动解析
- 零外部依赖设计

#### 内容提取 (`extractors/`)
- `BaseExtractor` 抽象基类
- `DocSendExtractor` - 支持 API + Playwright 降级
- `PDFExtractor` - 支持 pypdf + 可选 OCR
- `GoogleSlidesExtractor` - 导出 PDF 后提取

#### LLM 分析 (`llm/`)
- 两阶段架构（Router + Extractor）
- `LLMExtractor` 类实现
- 支持任意 OpenAI 兼容 API
- Prompt 模板和标签定义
- JSON 多重解析策略

#### 主入口
- `DealExtractor` 整合类
- 简洁的公开 API
- 自动 deck URL 分配

#### 文档
- README.md - 完整使用文档
- PRD.md - 产品需求文档
- CHANGELOG.md - 变更日志

### Technical Details

#### 从 Bot 项目学到的经验
1. 链接检测使用确定性逻辑，不依赖 LLM
2. DocSend CAPTCHA 用 docsend2pdf.com API 绑架
3. 两阶段 LLM 减少 token 消耗
4. 每个 deal 独立提取 external_source
5. 使用唯一 ID 确保并行安全

#### 代码来源参考
| 功能 | 来源文件 |
|------|----------|
| 链接检测 | `bot/utils/link_detector.py` |
| DocSend 提取 | `bot/extractors/docsend_extractor.py` |
| PDF OCR | `bot/extractors/pdf_extractor.py` |
| 两阶段架构 | `bot/agent/two_stage_agent.py` |
| Prompt 设计 | `bot/agent/prompts.py` |

---

## Version History

| Version | Date | Status |
|---------|------|--------|
| 0.1.0 | 2025-01-31 | 基础框架完成 |

---

## Migration Notes

### 从 Bot 迁移

如果你之前使用 `bot/` 中的代码，迁移步骤：

1. 安装依赖: `pip install -r deal_extractor/requirements.txt`

2. 替换导入:
```python
# 旧代码
from bot.utils.link_detector import LinkDetector
from bot.extractors.docsend_extractor import DocSendExtractor

# 新代码
from deal_extractor.links import LinkDetector
from deal_extractor.extractors import DocSendExtractor
```

3. 使用新的主入口:
```python
# 旧代码 - 手动组装
detector = LinkDetector()
docsend = DocSendExtractor(...)
pdf = PDFExtractor(...)
agent = TwoStageAgent(...)

# 新代码 - 一体化
extractor = DealExtractor(
    llm_api_key="...",
    docsend_email="...",
)
result = await extractor.extract(text, sender)
```

4. 数据模型变化:
```python
# 旧: ExtractedDeal (在 two_stage_agent.py)
# 新: Deal (在 models/types.py)

# 字段基本相同，可直接替换
```
