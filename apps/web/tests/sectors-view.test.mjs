import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const viewSource = await readFile(
  new URL('../src/views/SectorsView.vue', import.meta.url),
  'utf8',
)

test('sector ranking presents expected excess only as a model-level metric', () => {
  assert.match(viewSource, /模型 Top 20% 组合历史平均超额/)
  assert.match(viewSource, /sectorModelExpectedExcess/)
  assert.doesNotMatch(viewSource, /row\.expected_excess/)
  assert.doesNotMatch(viewSource, /<th[^>]*>预期超额<\/th>/)
})

test('sector ranking uses explicit audited freshness states instead of generic pending data', () => {
  assert.match(viewSource, /sectorFlowAuditStatus/)
  assert.match(viewSource, /sectorLeaderChangeAuditStatus/)
  assert.match(viewSource, /缺预测日可信龙头/)
  assert.match(viewSource, /已有 \$\{audit\.coverageDays\}\/\$\{audit\.windowDays\}日/)
  assert.doesNotMatch(viewSource, /待数据/)
})

test('sector ranking never reveals or links an unaudited or off-date leader', () => {
  assert.match(
    viewSource,
    /v-if="leaderChangeAvailable\(row\) && stockSymbol\(row\.leader_code\)"/,
  )
  assert.match(viewSource, /function leaderUnavailableLabel/)
  assert.match(viewSource, /异日龙头已隐藏/)
  assert.match(viewSource, /未审计龙头已隐藏/)
  assert.match(viewSource, /名称、涨幅和链接均已隐藏/)
})
