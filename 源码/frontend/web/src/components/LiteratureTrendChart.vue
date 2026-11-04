<template>
  <div v-if="series.length" class="literature-trend">
    <div class="literature-trend__layout">
      <div class="literature-trend__scroll" role="region" aria-label="各主题年度文献趋势，可横向滚动" tabindex="0">
        <div class="literature-trend__plot" :style="{ minWidth: `${years.length * Math.max(48, series.length * 14)}px` }">
          <div v-for="year in years" :key="year" class="literature-trend__year">
            <div class="literature-trend__bars"><i v-for="(item, index) in series" :key="item.label" :style="{ height: `${countFor(item, year) / maximum * 100}%`, background: colors[index % colors.length] }" :title="`${item.label} · ${year}年：${countFor(item, year)}篇`"><span class="sr-only">{{ item.label }} {{ year }} 年 {{ countFor(item, year) }} 篇</span></i></div>
            <small>{{ year }}</small>
          </div>
        </div>
      </div>
      <aside class="literature-trend__legend" aria-label="研究子方向图例"><span v-for="(item, index) in series" :key="item.label"><i :style="{ background: colors[index % colors.length] }"></i><b>{{ item.label }}</b></span></aside>
    </div>
  </div>
  <p v-else class="literature-trend__empty">当前没有足够的年度文献数据形成趋势图。</p>
</template>
<script setup lang="ts">
import { computed } from 'vue'
import type { TrendSeries } from '../services/resultSnapshot'
const props = defineProps<{ series: TrendSeries[] }>()
const colors = ['#4f81bd', '#4a9b91', '#c18b47', '#9274b9', '#b3687c']
const years = computed(() => [...new Set(props.series.flatMap(item => item.points.map(point => point.year)))].sort((a, b) => a - b))
const maximum = computed(() => Math.max(1, ...props.series.flatMap(item => item.points.map(point => point.count))))
function countFor(item: TrendSeries, year: number) { return item.points.find(point => point.year === year)?.count || 0 }
</script>
<style scoped>
.literature-trend{min-width:0}.literature-trend__layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(180px,260px);gap:18px;align-items:start}.literature-trend__scroll{max-width:100%;overflow-x:auto}.literature-trend__plot{display:flex;gap:12px;padding:12px 4px}.literature-trend__year{flex:1;min-width:36px;text-align:center}.literature-trend__bars{height:128px;display:flex;align-items:flex-end;justify-content:center;gap:3px;border-bottom:1px solid #dbe2ed}.literature-trend__bars>i{display:block;flex:1;max-width:20px;border-radius:3px 3px 0 0}.literature-trend__year small{color:#64748b}.literature-trend__legend{display:flex;flex-direction:column;gap:9px;margin:8px 0 0;padding:9px 0 0;border-top:0;font-size:12px;line-height:1.5}.literature-trend__legend span{display:flex;align-items:flex-start;gap:7px;min-width:0}.literature-trend__legend b{font-weight:400;overflow-wrap:anywhere}.literature-trend__legend i{width:10px;height:10px;flex:0 0 10px;margin-top:4px;border-radius:2px}.literature-trend p,.literature-trend__empty{font-size:12px;color:#64748b;line-height:1.6}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0,0,0,0)}
@media (max-width:640px){.literature-trend__layout{display:block}.literature-trend__legend{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px 12px;margin-top:10px;padding-top:10px;border-top:1px solid #e3e8f1}}
</style>
