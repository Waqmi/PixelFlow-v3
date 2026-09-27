<script setup lang="ts">
defineProps<{
  expectedWorkerVersion: string
  workerVersion: string
  workerBuildHash: string
  workerRulesVersion: string
  workerReadyState: string
}>()

const emit = defineEmits<{
  (event: 'copy-diagnostics'): void
  (event: 'export-diagnostics'): void
}>()
</script>

<template>
  <section class="settings-page-intro"><h3>关于 PixelFlow</h3><p>轻量、离线优先的电商主图整理工具，帮助你把原始素材整理成可直接检查的电商图片。</p></section>
  <dl class="about-list">
    <div><dt>版本号</dt><dd>{{ expectedWorkerVersion }}</dd></div>
    <div><dt>Worker 版本</dt><dd>{{ workerVersion }}</dd></div>
    <div><dt>Worker 哈希</dt><dd>{{ workerBuildHash ? workerBuildHash.slice(0, 8) : '未提供' }}</dd></div>
    <div><dt>规则版本</dt><dd>{{ workerRulesVersion }}</dd></div>
    <div><dt>Worker 状态</dt><dd>{{ workerReadyState }}</dd></div>
    <div><dt>构建日期</dt><dd>2026-09-19</dd></div>
    <div><dt>这是什么</dt><dd>PixelFlow 用于批量整理电商商品素材，按尺寸、品类模板和品牌 Logo 输出主图、透明产品图、唯品专享图及模特主图。</dd></div>
    <div><dt>怎么使用</dt><dd>选择素材目录和输出目录 → 选择品类、尺寸及输出选项 → 必要时在素材确认中修正分类 → 点击生成，完成后可直接打开输出文件夹检查结果。</dd></div>
    <div><dt>素材目录</dt><dd>支持图片直接放在根目录，也支持按颜色或款式放在子文件夹中；支持 JPG、JPEG、PNG。建议同一商品的图片放在同一文件夹，文件名尽量保留颜色或款式信息。</dd></div>
    <div><dt>使用技巧</dt><dd>白底商品图适合生成普通主图；细节图可用于辅助判断；模特图请在文件名或文件夹名中标注“模特、上身、穿搭、model、lookbook”等关键词。Logo 请使用完整尺寸的 RGBA PNG，并按浅色背景/深色背景选择对应版本。文件命名建议使用简短模式，完整原名可在输出清单中追溯。</dd></div>
    <div><dt>功能边界</dt><dd>模特图只做分类和安全构图放大，保留原图内容，不凭空生成新人物或新场景。本地 ONNX 模型只提供人物位置和构图建议，无法使用时自动回退规则模式；未启用远程辅助时无需 API Key，图片处理在本机完成。</dd></div>
    <div><dt>最新版本</dt><dd><a href="https://github.com/Waqmi/PixelFlow-Open/releases" target="_blank">https://github.com/Waqmi/PixelFlow-Open/releases</a>（点击查看和下载最新发布版本）</dd></div>
  </dl>
  <div class="settings-actions">
    <button class="text-button" @click="emit('copy-diagnostics')">复制诊断信息</button>
    <button class="text-button" @click="emit('export-diagnostics')">导出诊断包</button>
  </div>
</template>
