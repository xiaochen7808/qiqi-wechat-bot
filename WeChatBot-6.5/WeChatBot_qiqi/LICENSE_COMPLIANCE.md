# 许可证合规说明 / License Compliance Guide

## 项目许可证 / Project License

QIQI 微信 Bot 遵循 **GNU GPL-3.0 或更高版本** 许可证，并保留 KouriChat、umaru 与 iwyxdxl 的来源和版权信息。

## 依赖库许可证 / Dependencies License

### 核心依赖 (必需)
所有核心依赖库均使用与GPL-3.0兼容的许可证：
- Flask, SQLAlchemy, Requests, BeautifulSoup4 等使用 MIT/BSD/Apache 2.0 许可证

### 微信自动化库

当前发行包直接使用 **wxautox4_wechatbot 40.1.10** 定制 wheel。该组件使用独立的私有许可证，不属于本仓库 GPL 源代码的自动授权范围；当前代码也没有实现到开源 `wxauto` 的自动兼容回退。

运行 `Run.bat` 会按 Python 3.9-3.12 安装对应 wheel。使用或重新分发前，应确认组件权利人授予的授权范围，并保留组件自身的许可证或授权说明。

## GPL-3.0 合规性 / GPL-3.0 Compliance

1. **版权保留**：保留原作者、历史修改者及其版权声明
2. **修改标识**：明确标注 QIQI 微信 Bot 6.9 的修改和发行信息
3. **源代码提供**：分发二进制或压缩包时同步提供 GPL 覆盖代码的对应源代码
4. **许可证提供**：发行包保留完整 `LICENSE`、依赖声明和构建/安装材料
5. **依赖分离**：私有 wheel 的使用和分发权由其独立许可证决定

## 分发说明 / Distribution Notes

- 分发本项目时，必须包含完整的源代码
- 必须保留所有许可证文件和声明
- 任何修改都必须遵循GPL-3.0条款
- 必须保留 KouriChat、umaru 和 iwyxdxl 的来源及历史版权信息
- 私有 wheel 需另行确认使用与再分发授权

## 法律声明 / Legal Notice

GPL-3.0-or-later 适用于本项目中受 GPL 覆盖的代码。私有依赖仍按其自身许可证处理，不能用本项目的 GPL 声明替代该依赖的授权文件。

---
更新日期：2026年7月  
历史修改版本维护者：iwyxdxl  
当前发行品牌：QIQI 微信 Bot 6.9
