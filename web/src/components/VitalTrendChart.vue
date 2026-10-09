<script setup>
import { computed } from 'vue'
import dayjs from 'dayjs'
import { use } from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, LegendComponent, MarkPointComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import VChart from 'vue-echarts'

use([LineChart, GridComponent, LegendComponent, TooltipComponent, MarkPointComponent, CanvasRenderer])

// 体征折线图。数据来自 /health/trend：每个可视化点对应一个时间桶的平均值。
// 老人详情页按 6 分钟分桶，因此曲线上每 6 分钟最多出现一个点；断档处不硬连。
const props = defineProps({
  trend: { type: Object, default: null },
  metric: { type: String, default: 'heart_rate' },
  secondaryTrend: { type: Object, default: null },
  secondaryMetric: { type: String, default: '' },
  loading: { type: Boolean, default: false },
  height: { type: Number, default: 320 },
  // 没有点时显示的说明；不传就按"这段时间内没有读数"
  emptyText: { type: String, default: '' },
})

const METRIC_STYLE = {
  heart_rate: { color: '#d4605a', decimals: 0 },
  spo2: { color: '#2f9e93', decimals: 0 },
  systolic: { color: '#d99a3c', decimals: 0 },
  diastolic: { color: '#b07a2c', decimals: 0 },
  temperature: { color: '#7a5fb5', decimals: 1 },
}

const style = computed(() => METRIC_STYLE[props.metric] || METRIC_STYLE.heart_rate)
const points = computed(() => props.trend?.points || [])
const secondaryPoints = computed(() => props.secondaryTrend?.points || [])
const hasPoints = computed(() => points.value.length || secondaryPoints.value.length)
const unit = computed(() => props.trend?.unit || '')
const label = computed(() => props.trend?.label || '')

function fmt(value) {
  if (value == null) return '--'
  return Number(value).toFixed(style.value.decimals)
}

function hexToRgba(hex, alpha) {
  const value = hex.replace('#', '')
  const r = parseInt(value.slice(0, 2), 16)
  const g = parseInt(value.slice(2, 4), 16)
  const b = parseInt(value.slice(4, 6), 16)
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

/** 把桶列表转换为折线点 [时间戳, 平均值]；数据断档处插一个 null 让线断开。 */
function buildLine(data, bucketSeconds) {
  const line = []
  const meta = []
  let previousEnd = null
  const push = (stamp, value, point) => {
    line.push([stamp, value])
    meta.push(point)
  }
  for (const point of data) {
    const bucketTime = new Date(point.t).getTime()
    if (previousEnd != null && bucketTime - previousEnd > bucketSeconds * 2000) {
      push(bucketTime - 1, null, null)
    }
    previousEnd = bucketTime
    push(bucketTime, point.avg, point)
  }
  return { line, meta }
}

const option = computed(() => {
  const data = points.value
  const color = style.value.color
  const hours = props.trend?.hours || 24
  const { line, meta } = buildLine(data, props.trend?.bucket_seconds || 60)
  const secondaryStyle = METRIC_STYLE[props.secondaryMetric] || METRIC_STYLE.diastolic
  const secondaryData = secondaryPoints.value
  const secondary = buildLine(secondaryData, props.secondaryTrend?.bucket_seconds || 60)
  const values = [...data, ...secondaryData].flatMap((p) => [p.min, p.max])
  const lo = values.length ? Math.min(...values) : 0
  const hi = values.length ? Math.max(...values) : 1
  const pad = Math.max((hi - lo) * 0.15, style.value.decimals ? 0.2 : 2)
  const now = Date.now()
  const makeSeries = (trend, metricStyle, seriesLine, seriesMeta) => {
    const lastPoint = [...seriesLine].reverse().find((item) => item[1] != null)
    const decimals = metricStyle.decimals
    const formatValue = (value) => value == null ? '--' : Number(value).toFixed(decimals)
    return {
      name: trend?.label || '读数',
      type: 'line',
      data: seriesLine,
      _meta: seriesMeta,
      connectNulls: false,
      showSymbol: seriesLine.length <= 800,
      symbol: 'circle',
      symbolSize: seriesLine.length > 300 ? 3 : 5,
      lineStyle: { width: 2.2, color: metricStyle.color },
      itemStyle: { color: metricStyle.color },
      areaStyle: props.secondaryTrend ? undefined : {
        color: {
          type: 'linear',
          x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [
            { offset: 0, color: hexToRgba(metricStyle.color, 0.18) },
            { offset: 1, color: hexToRgba(metricStyle.color, 0) },
          ],
        },
      },
      emphasis: { focus: 'none', scale: 1.4 },
      markPoint: lastPoint
        ? {
            symbol: 'circle',
            symbolSize: 10,
            itemStyle: { color: metricStyle.color, borderColor: '#fff', borderWidth: 2 },
            label: {
              show: true,
              position: 'top',
              distance: 7,
              color: '#26302b',
              fontSize: 12,
              fontWeight: 600,
              formatter: () => `${formatValue(trend?.latest ?? lastPoint[1])} ${trend?.unit || ''}`,
            },
            data: [{ coord: [lastPoint[0], lastPoint[1]] }],
          }
        : undefined,
    }
  }

  const series = [makeSeries(props.trend, style.value, line, meta)]
  if (props.secondaryTrend) {
    series.push(makeSeries(props.secondaryTrend, secondaryStyle, secondary.line, secondary.meta))
  }

  return {
    animationDuration: 500,
    animationDurationUpdate: 350,
    color: series.map((item) => item.lineStyle.color),
    legend: props.secondaryTrend
      ? { top: 8, right: 16, itemWidth: 18, itemHeight: 3, textStyle: { color: '#5d6862' } }
      : undefined,
    grid: { left: 14, right: 22, top: props.secondaryTrend ? 38 : 26, bottom: 10, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: 'rgba(38, 48, 43, 0.94)',
      borderWidth: 0,
      textStyle: { color: '#fff', fontSize: 14 },
      axisPointer: { type: 'line', lineStyle: { color: hexToRgba(color, 0.5) } },
      formatter: (params) => {
        const hits = params.filter((item) => item.value?.[1] != null)
        if (!hits.length) return ''
        const stamp = hits[0].value[0]
        const when = dayjs(stamp).format(hours > 24 ? 'MM-DD HH:mm:ss' : 'HH:mm:ss')
        const lines = [`<b>${when}</b>`]
        hits.forEach((hit) => {
          const currentSeries = series[hit.seriesIndex]
          const currentTrend = hit.seriesIndex === 0 ? props.trend : props.secondaryTrend
          const currentStyle = hit.seriesIndex === 0 ? style.value : secondaryStyle
          const value = Number(hit.value[1]).toFixed(currentStyle.decimals)
          lines.push(`${currentSeries.name} ${value} ${currentTrend?.unit || ''}`)
        })
        return lines.join('<br/>')
      },
    },
    xAxis: {
      type: 'time',
      min: now - hours * 3600 * 1000,
      max: now,
      axisLine: { lineStyle: { color: '#ebe5db' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#5d6862',
        fontSize: 13,
        hideOverlap: true,
        formatter: (value) => dayjs(value).format(hours > 24 ? 'MM-DD HH:mm' : 'HH:mm'),
      },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      min: Math.floor((lo - pad) * (style.value.decimals ? 10 : 1)) / (style.value.decimals ? 10 : 1),
      max: Math.ceil((hi + pad) * (style.value.decimals ? 10 : 1)) / (style.value.decimals ? 10 : 1),
      axisLabel: { color: '#5d6862', fontSize: 13, formatter: (value) => fmt(value) },
      splitLine: { lineStyle: { color: '#f3efe7' } },
    },
    series,
  }
})
</script>

<template>
  <div class="trend" :style="{ height: `${height}px` }">
    <VChart v-if="hasPoints" :option="option" autoresize class="chart" />
    <div v-else class="empty">
      <el-empty
        :description="emptyText || (loading ? '正在读取曲线…' : `这段时间内没有${label || '该项'}读数`)"
        :image-size="64"
      />
    </div>
  </div>
</template>

<style scoped>
.trend {
  position: relative;
  width: 100%;
  border-radius: var(--radius);
  background: var(--surface-tint);
  border: 1px solid var(--line);
  overflow: hidden;
}

.chart,
.empty {
  width: 100%;
  height: 100%;
}

.empty {
  display: grid;
  place-items: center;
}
</style>
