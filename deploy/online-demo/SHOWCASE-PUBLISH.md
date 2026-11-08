# 静态作品首页的发布准备

作品文件为 `docs/showcase/index.html` 和 `style.css`，可直接在浏览器本地打开。它不包含模型服务、业务后端、邀请口令或演示录像。GitHub/Hugging Face 发布的是项目说明入口，不能据此宣称实时 AI 已部署。

## GitHub Pages

只将上述两个文件及空 `.nojekyll` 放到指定静态发布目录/分支，保持相对路径。发布来源由仓库持有人在 Pages 设置中选定；不要上传整个工作区、reports、tmp、私密环境文件、模型或个人资料。确认账户方案对当前私有仓库的 Pages 支持；不要为绕过限制自行公开源码仓库。

准备阶段的源码经 `codex/client-handover-20261108` 分支审查；持有人已授权公开净源码与作品页。源码合并、仓库可见性及作品站发布仍分别核对，不能把已有源码上传当成面试官已能匿名查看。页面注明源码访问以 GitHub 权限为准。

## Hugging Face Static Space

选择 Static HTML SDK，仅上传首页、样式和带如下 metadata 的 README.md：

```yaml
---
title: Property AI Portfolio
sdk: static
app_file: index.html
---
```

此方式不需要运行 Docker、Gradio 或 GPU；不要为静态说明页开启计费硬件。对外可见性由持有人批准。若以后增加真实推理，需另行评估账号条件、计算资源和访问权限，不能因为静态页已发布就自动开放本机 GPU。

## 发布后的最小检查

从持有人未登录的浏览器核对：页面可打开、样式正常、源码访问说明准确、手机不横向溢出、页面没有外部请求或虚假的“开始推理”按钮。当前页面仍写“受邀远程体验尚未开放”；只有实际入口通过核对后才更新状态，私密链接和口令不写入此公开页。

已有本地检查只覆盖静态文件的桌面/手机显示、锚点、键盘详情和无远端请求，不代替托管发布或真实 AI 体验验收。当前没有已发布的作品网址。

参考：[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview)。当前套餐/权限以平台实际账户为准，不沿用旧教程的免费计算承诺。
