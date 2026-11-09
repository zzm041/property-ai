# 静态作品首页的发布准备

作品文件为 `docs/showcase/index.html` 和 `style.css`，可直接在浏览器本地打开。它不包含模型服务、业务后端、邀请口令或演示录像。GitHub/Hugging Face 发布的是项目说明入口，不能据此宣称实时 AI 已部署。

## GitHub Pages

只将上述两个文件及空 `.nojekyll` 放到指定静态发布目录/分支，保持相对路径。发布来源由仓库持有人在 Pages 设置中选定；不要上传整个工作区、reports、tmp、私密环境文件、模型或个人资料。若将此方案用于私有仓库，需先确认账户方案支持 Pages。

作品页发布在 https://zzm041.github.io/property-ai/showcase/ ，源码位于 main。GitHub Pages 选择 main 的 /docs 目录，页面路径为 /showcase/。公开页面介绍产品功能和部署文档；私密入口与运行口令单独管理。

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

使用未登录浏览器核对：页面可打开、样式正常、源码链接有效、手机不横向溢出。页面只加载自身静态文件，不请求业务或模型服务。当前“在线交互暂未开放”状态须在实际入口完成验证后才更新，私密链接和口令不写入公开页。

2026-11-09 已完成首次公开页面访问核对。页面更新后需检查最新部署与静态文件；真实 AI 交互验证另行执行。

参考：[GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/what-is-github-pages)、[Hugging Face Spaces](https://huggingface.co/docs/hub/spaces-overview)。当前套餐/权限以平台实际账户为准，不沿用旧教程的免费计算承诺。
