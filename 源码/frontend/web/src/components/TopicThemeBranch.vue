<template>
  <li><b :title="node.label">{{ node.display_label || node.label }}</b><small>{{ node.discipline_tags?.join('、') || '学科待检索核验' }}</small><span>{{ node.boundary }}</span><details v-if="node.display_label && node.display_label !== node.label"><summary>完整主题名称</summary><span>{{ node.label }}</span></details><ul v-if="node.children.length"><TopicThemeBranch v-for="child in node.children" :key="child.theme_id" :node="child" /></ul></li>
</template>
<script setup lang="ts">
defineProps<{ node: Record<string, any> }>()
</script>
