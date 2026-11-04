<template>
  <div class="result-disclosure">
    <button type="button" :aria-expanded="open" :aria-controls="bodyId" @click="open = !open">
      {{ open ? collapseLabel : label }}<span class="material-icons-outlined" aria-hidden="true">{{ open ? 'expand_less' : 'expand_more' }}</span>
    </button>
    <div v-if="open" :id="bodyId" class="result-disclosure__body"><slot /></div>
  </div>
</template>
<script setup lang="ts">
import { ref, useId } from 'vue'
defineProps<{ label: string; collapseLabel: string }>()
const open = ref(false)
const bodyId = useId()
</script>
<style scoped>
.result-disclosure{min-width:0;margin-top:10px;text-align:left}
.result-disclosure>button{display:inline-flex;align-items:center;gap:3px;max-width:100%;padding:0;border:0;background:none;color:#2465ae;font:inherit;font-size:14px;line-height:1.8;text-align:left;cursor:pointer}
.result-disclosure>button:hover{color:#174c87}.result-disclosure>button:focus-visible{outline:2px solid #2465ae;outline-offset:3px}
.result-disclosure>button>span{flex:none;font-size:18px}.result-disclosure__body{min-width:0;margin-top:10px}
</style>
