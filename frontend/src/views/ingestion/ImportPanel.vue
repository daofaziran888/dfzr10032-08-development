<template>
  <section class="import-panel">
    <header class="panel-head">
      <div>
        <h3>上传文件接入</h3>
        <p class="page-desc">
          按通道声明的输入格式导入上传文件，形成接入原始事件数据集；每条事件保留文件标识与原始行号，
          坏记录留存位置和原文、不会被当成有效事件，重投修正时已确认行自动跳过、不重复交付。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="openResults">解析明细核对</button>
      </div>
    </header>

    <form class="import-form" @submit.prevent="submitImport">
      <label class="modal-field">
        <span>接入通道（按通道声明的输入格式解析）</span>
        <select v-model.number="form.channelId" required>
          <option v-for="ch in channels" :key="ch.id" :value="ch.id">
            {{ ch['通道编号'] }} · {{ ch['通道名称'] }} · 格式：{{ ch['输入格式'] }}
          </option>
        </select>
      </label>
      <label class="modal-field">
        <span>原始文件名</span>
        <input v-model="form.filename" placeholder="如 freight.csv" />
      </label>
      <label class="modal-field">
        <span>交付策略（部分行解析失败时如何处理）</span>
        <select v-model="form.delivery_policy">
          <option value="可用先行">可用先行：好行先交付，坏行挂起等修正</option>
          <option value="完整交付">完整交付：存在坏行则整份阻断，修正后释放</option>
        </select>
      </label>
      <label class="modal-field full">
        <span>文件内容（每行一条记录；可粘入含坏行的样本，如中间一行缺少闭合引号）</span>
        <textarea
          v-model="form.content"
          rows="6"
          :placeholder="samplePlaceholder"
          required
        ></textarea>
      </label>
      <div class="import-actions">
        <button class="btn primary" type="submit" :disabled="uploading">
          {{ uploading ? '导入中…' : '导入文件' }}
        </button>
        <button class="btn ghost" type="button" @click="fillSample">填入三行货运样本（含坏行）</button>
        <span v-if="message" :class="['form-message', { bad: !lastOk }]">{{ message }}</span>
      </div>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>文件标识</th>
          <th>原始文件名</th>
          <th>通道</th>
          <th>输入格式</th>
          <th>交付策略</th>
          <th>总行数</th>
          <th>坏记录</th>
          <th>跳过行</th>
          <th>状态</th>
          <th>上传时间</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="file in files" :key="file.id">
          <td>{{ file['文件标识'] }}</td>
          <td>{{ file['原始文件名'] }}</td>
          <td>{{ file['通道编号'] }}</td>
          <td>{{ file['输入格式'] }}</td>
          <td>{{ file['交付策略'] }}</td>
          <td>{{ file['总行数'] }}</td>
          <td>
            <span :class="['badge', file['坏记录数'] ? 'badge-bad' : 'badge-ok']">{{ file['坏记录数'] }}</span>
          </td>
          <td>{{ file['跳过行数'] }}</td>
          <td><span :class="['badge', fileStatusClass(file['状态'])]">{{ file['状态'] }}</span></td>
          <td>{{ file['上传时间'] }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openEvents(file)">事件明细</button>
            <button class="link" type="button" @click="openDeadLetters(file)">坏记录追查</button>
            <button class="link" type="button" @click="openReimport(file)">重投修正</button>
            <button class="link" type="button" @click="openParse(file)">交解析器</button>
          </td>
        </tr>
        <tr v-if="!files.length">
          <td colspan="11" class="empty-state">暂无接入文件，先在上方导入一份上传文件</td>
        </tr>
      </tbody>
    </table>

    <!-- 事件明细 / 坏记录追查 -->
    <Teleport to="body">
      <div v-if="eventsModal.show" class="modal-overlay" @click.self="eventsModal.show = false">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>{{ eventsModal.title }}（{{ eventsModal.file?.['文件标识'] }}）</h3>
            <button class="btn ghost" type="button" @click="eventsModal.show = false">&times;</button>
          </header>
          <div class="modal-body">
            <div class="modal-toolbar">
              <label class="modal-field">
                <span>按状态筛选</span>
                <select v-model="eventsModal.status" @change="loadEvents">
                  <option value="">全部状态</option>
                  <option v-for="status in eventStatuses" :key="status" :value="status">{{ status }}</option>
                </select>
              </label>
            </div>
            <table class="data-table compact">
              <thead>
                <tr>
                  <th>原始行号</th>
                  <th>物理行号</th>
                  <th>状态</th>
                  <th>原文</th>
                  <th>错误原因 / 字段快照</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="event in eventsModal.items" :key="event.id" :class="rowClass(event['状态'])">
                  <td>{{ event['原始行号'] }}</td>
                  <td>{{ event['物理行号'] }}</td>
                  <td><span :class="['badge', eventStatusClass(event['状态'])]">{{ event['状态'] }}</span></td>
                  <td><code>{{ event['原文'] }}</code></td>
                  <td>
                    <span v-if="event['错误原因']" class="error-text">{{ event['错误原因'] }}</span>
                    <span v-else-if="event['字段快照']">{{ formatSnapshot(event['字段快照']) }}</span>
                    <span v-else>—</span>
                  </td>
                </tr>
                <tr v-if="!eventsModal.items.length">
                  <td colspan="5" class="empty-state">没有符合条件的事件</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- 重投修正文件 -->
    <Teleport to="body">
      <div v-if="reimportModal.show" class="modal-overlay" @click.self="reimportModal.show = false">
        <div class="modal-card">
          <header class="modal-head">
            <h3>重投修正文件（{{ reimportModal.file?.['文件标识'] }}）</h3>
            <button class="btn ghost" type="button" @click="reimportModal.show = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitReimport">
            <p class="modal-tip">
              已确认的行号再次出现会自动跳过、不会二次交付；修正内容按下方起始行号沿用原始定位。
            </p>
            <label class="modal-field">
              <span>首行对应的原始行号</span>
              <input v-model.number="reimportModal.lineOffset" type="number" min="1" required />
            </label>
            <label class="modal-field">
              <span>修正文件名</span>
              <input v-model="reimportModal.filename" placeholder="如 freight-fixed.csv" />
            </label>
            <label class="modal-field">
              <span>修正内容（每行一条）</span>
              <textarea v-model="reimportModal.content" rows="6" required></textarea>
            </label>
            <footer class="modal-foot">
              <button class="btn" type="button" @click="reimportModal.show = false">取消</button>
              <button class="btn primary" type="submit">提交修正文件</button>
            </footer>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 交格式解析器 -->
    <Teleport to="body">
      <div v-if="parseModal.show" class="modal-overlay" @click.self="parseModal.show = false">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>格式解析器取数（{{ parseModal.file?.['文件标识'] }}）</h3>
            <button class="btn ghost" type="button" @click="parseModal.show = false">&times;</button>
          </header>
          <form class="modal-body" @submit.prevent="submitParse">
            <p class="modal-tip">解析器只读取「已确认」的获准事件，坏记录与已解析事件不会重复交付；明细回指文件标识与原始行号。</p>
            <label class="modal-field">
              <span>解析配置</span>
              <select v-model.number="parseModal.parserId" required>
                <option v-for="parser in parsers" :key="parser.id" :value="parser.id">
                  {{ parser['解析编号'] }} · {{ parser['输入格式'] }} → {{ parser['输出格式'] }}
                </option>
              </select>
            </label>
            <label class="modal-check">
              <input v-model="parseModal.scopeFile" type="checkbox" />
              <span>仅解析本文件的获准事件（取消则取整个通道）</span>
            </label>
            <footer class="modal-foot">
              <button class="btn primary" type="submit">执行解析</button>
            </footer>
            <table v-if="parseModal.details.length" class="data-table compact">
              <thead>
                <tr>
                  <th>解析批次</th>
                  <th>文件标识</th>
                  <th>原始行号</th>
                  <th>原文</th>
                  <th>解析结果</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="detail in parseModal.details" :key="detail.id">
                  <td>{{ detail['解析批次'] }}</td>
                  <td>{{ detail['文件标识'] }}</td>
                  <td>{{ detail['原始行号'] }}</td>
                  <td><code>{{ detail['原文'] }}</code></td>
                  <td>{{ formatSnapshot(detail['解析结果']) }}</td>
                </tr>
              </tbody>
            </table>
          </form>
        </div>
      </div>
    </Teleport>

    <!-- 解析明细核对 -->
    <Teleport to="body">
      <div v-if="resultsModal.show" class="modal-overlay" @click.self="resultsModal.show = false">
        <div class="modal-card modal-wide">
          <header class="modal-head">
            <h3>解析明细（按原位置核对）</h3>
            <button class="btn ghost" type="button" @click="resultsModal.show = false">&times;</button>
          </header>
          <div class="modal-body">
            <div class="modal-toolbar">
              <label class="modal-field">
                <span>接入通道</span>
                <select v-model.number="resultsModal.channelId" @change="loadResults">
                  <option v-for="ch in channels" :key="ch.id" :value="ch.id">{{ ch['通道编号'] }}</option>
                </select>
              </label>
            </div>
            <table class="data-table compact">
              <thead>
                <tr>
                  <th>解析批次</th>
                  <th>文件标识</th>
                  <th>原始行号</th>
                  <th>输出格式</th>
                  <th>解析结果</th>
                  <th>解析时间</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="detail in resultsModal.items" :key="detail.id">
                  <td>{{ detail['解析批次'] }}</td>
                  <td>{{ detail['文件标识'] }}</td>
                  <td>{{ detail['原始行号'] }}</td>
                  <td>{{ detail['输出格式'] }}</td>
                  <td>{{ formatSnapshot(detail['解析结果']) }}</td>
                  <td>{{ detail['解析时间'] }}</td>
                </tr>
                <tr v-if="!resultsModal.items.length">
                  <td colspan="6" class="empty-state">该通道暂无解析明细</td>
                </tr>
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </Teleport>
  </section>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/ingestion/imports'

interface ChannelRow {
  id: number
  通道编号: string
  通道名称: string
  输入格式: string
}
interface ParserRow {
  id: number
  解析编号: string
  输入格式: string
  输出格式: string
}
interface FileRow {
  id: number
  文件标识: string
  原始文件名: string
  通道id: number
  通道编号: string
  输入格式: string
  交付策略: string
  总行数: number
  坏记录数: number
  跳过行数: number
  状态: string
  上传时间: string
}
interface EventRow {
  id: number
  原始行号: number
  物理行号: number
  状态: string
  原文: string
  错误原因: string | null
  字段快照: Record<string, string> | null
}
interface ParseDetail {
  id: number
  解析批次: string
  文件标识: string
  原始行号: number
  原文: string
  输出格式: string
  解析结果: Record<string, string>
  解析时间: string
}

const samplePlaceholder = [
  'YD20260001,上海,北京,精密仪器,120',
  'YD20260002,广州,成都,"电子元件,80',
  'YD20260003,深圳,杭州,机械配件,260',
].join('\n')
const sampleContent = samplePlaceholder

const channels = ref<ChannelRow[]>([])
const parsers = ref<ParserRow[]>([])
const files = ref<FileRow[]>([])
const uploading = ref(false)
const message = ref('')
const lastOk = ref(true)

const form = reactive({
  channelId: 0,
  filename: '',
  content: '',
  delivery_policy: '可用先行',
})

const eventStatuses = ['已确认', '坏记录', '待投递', '已跳过', '已修正', '空白跳过']

const eventsModal = reactive<{ show: boolean; title: string; file: FileRow | null; status: string; items: EventRow[] }>({
  show: false,
  title: '',
  file: null,
  status: '',
  items: [],
})

const reimportModal = reactive<{ show: boolean; file: FileRow | null; content: string; filename: string; lineOffset: number }>({
  show: false,
  file: null,
  content: '',
  filename: '',
  lineOffset: 1,
})

const parseModal = reactive<{ show: boolean; file: FileRow | null; parserId: number; scopeFile: boolean; details: ParseDetail[] }>({
  show: false,
  file: null,
  parserId: 0,
  scopeFile: true,
  details: [],
})

const resultsModal = reactive<{ show: boolean; channelId: number; items: ParseDetail[] }>({
  show: false,
  channelId: 0,
  items: [],
})

async function getJson<T>(path: string): Promise<{ items: T[] }> {
  const response = await request(path)
  if (!response.ok) {
    throw new Error(`接口返回 ${response.status}`)
  }
  return response.json()
}

async function loadChannels() {
  const data = await getJson<ChannelRow>('/api/ingestion?size=200')
  channels.value = data.items
  if (!form.channelId && channels.value.length) {
    form.channelId = channels.value[0].id
  }
  resultsModal.channelId = form.channelId
}

async function loadParsers() {
  const data = await getJson<ParserRow>('/api/parser?size=200')
  parsers.value = data.items
  if (parsers.value.length) {
    parseModal.parserId = parsers.value[0].id
  }
}

async function loadFiles() {
  const data = await getJson<FileRow>(`${ENDPOINT}/files`)
  files.value = data.items
}

function fillSample() {
  form.content = sampleContent
  form.filename = form.filename || 'freight.csv'
}

async function submitImport() {
  uploading.value = true
  message.value = ''
  try {
    const response = await request(`${ENDPOINT}/channels/${form.channelId}/files`, {
      method: 'POST',
      body: JSON.stringify({
        filename: form.filename,
        content: form.content,
        delivery_policy: form.delivery_policy,
      }),
    })
    const data = await response.json()
    lastOk.value = Boolean(data.ok)
    message.value = data.message || '导入失败'
    if (data.ok) {
      form.content = ''
      await loadFiles()
    }
  } catch (error) {
    lastOk.value = false
    message.value = error instanceof Error ? error.message : '导入请求失败'
  } finally {
    uploading.value = false
  }
}

async function openEvents(file: FileRow) {
  eventsModal.show = true
  eventsModal.title = '接入原始事件明细'
  eventsModal.file = file
  eventsModal.status = ''
  eventsModal.items = []
  await loadEvents()
}

async function openDeadLetters(file: FileRow) {
  eventsModal.show = true
  eventsModal.title = '坏记录追查（位置与原文留痕）'
  eventsModal.file = file
  eventsModal.status = '坏记录'
  eventsModal.items = []
  await loadEvents()
}

async function loadEvents() {
  if (!eventsModal.file) {
    return
  }
  const statusQuery = eventsModal.status ? `?status=${encodeURIComponent(eventsModal.status)}` : ''
  const data = await getJson<EventRow>(`${ENDPOINT}/files/${eventsModal.file.id}/events${statusQuery}`)
  eventsModal.items = data.items
}

function openReimport(file: FileRow) {
  reimportModal.show = true
  reimportModal.file = file
  reimportModal.content = ''
  reimportModal.filename = ''
  reimportModal.lineOffset = 1
}

async function submitReimport() {
  if (!reimportModal.file) {
    return
  }
  try {
    const response = await request(`${ENDPOINT}/files/${reimportModal.file.id}/reimport`, {
      method: 'POST',
      body: JSON.stringify({
        filename: reimportModal.filename,
        content: reimportModal.content,
        line_offset: reimportModal.lineOffset,
      }),
    })
    const data = await response.json()
    if (!data.ok) {
      throw new Error(data.message || '重投失败')
    }
    message.value = data.message
    lastOk.value = true
    reimportModal.show = false
    await loadFiles()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '重投请求失败'
    lastOk.value = false
  }
}

function openParse(file: FileRow) {
  parseModal.show = true
  parseModal.file = file
  parseModal.scopeFile = true
  parseModal.details = []
}

async function submitParse() {
  if (!parseModal.file) {
    return
  }
  try {
    const response = await request(`${ENDPOINT}/channels/${parseModal.file['通道id']}/parse`, {
      method: 'POST',
      body: JSON.stringify({
        parser_id: parseModal.parserId,
        file_id: parseModal.scopeFile ? parseModal.file.id : null,
      }),
    })
    const data = await response.json()
    if (!data.ok) {
      throw new Error(data.message || '解析失败')
    }
    parseModal.details = ((data.entry?.明细 ?? []) as ParseDetail[])
    message.value = data.message
    lastOk.value = true
  } catch (error) {
    message.value = error instanceof Error ? error.message : '解析请求失败'
    lastOk.value = false
  }
}

async function openResults() {
  resultsModal.show = true
  resultsModal.channelId = form.channelId
  resultsModal.items = []
  await loadResults()
}

async function loadResults() {
  if (!resultsModal.channelId) {
    return
  }
  const data = await getJson<ParseDetail>(`${ENDPOINT}/parse-results?channel_id=${resultsModal.channelId}`)
  resultsModal.items = data.items
}

function formatSnapshot(snapshot: unknown): string {
  if (!snapshot || typeof snapshot !== 'object') {
    return String(snapshot ?? '—')
  }
  return Object.entries(snapshot as Record<string, unknown>)
    .map(([key, value]) => `${key}=${String(value)}`)
    .join('，')
}

function eventStatusClass(status: string): string {
  if (status === '坏记录') return 'badge-bad'
  if (status === '已确认') return 'badge-ok'
  if (status === '待投递') return 'badge-warn'
  if (status === '已修正') return 'badge-info'
  return 'badge-muted'
}

function fileStatusClass(status: string): string {
  if (status === '已阻断') return 'badge-bad'
  if (status === '部分已交付') return 'badge-warn'
  return 'badge-ok'
}

function rowClass(status: string): string {
  if (status === '坏记录') return 'row-bad'
  if (status === '已跳过') return 'row-skip'
  return ''
}

onMounted(async () => {
  try {
    await Promise.all([loadChannels(), loadParsers(), loadFiles()])
  } catch (error) {
    message.value = error instanceof Error ? error.message : '接入面板初始化失败'
    lastOk.value = false
  }
})
</script>

<style scoped>
.import-panel {
  margin-top: 24px;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 16px;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.panel-head h3 {
  margin: 0 0 4px;
}
.import-form {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 12px;
  margin: 12px 0 16px;
  padding: 14px;
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 8px;
}
.import-form .full {
  grid-column: 1 / -1;
}
.import-form select,
.import-form input,
.import-form textarea {
  width: 100%;
  box-sizing: border-box;
  padding: 7px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
  font-family: inherit;
}
.import-actions {
  grid-column: 1 / -1;
  display: flex;
  align-items: center;
  gap: 10px;
}
.form-message {
  font-size: 13px;
  color: #067647;
}
.form-message.bad {
  color: #b42318;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font-size: 12px;
  white-space: nowrap;
}
.badge-ok {
  background: #e7f6ec;
  color: #067647;
}
.badge-bad {
  background: #fde8e8;
  color: #b42318;
}
.badge-warn {
  background: #fef3e0;
  color: #b54708;
}
.badge-info {
  background: #e8f0fe;
  color: #1d4ed8;
}
.badge-muted {
  background: #f1f5f9;
  color: #64748b;
}
.data-table.compact th,
.data-table.compact td {
  padding: 6px 8px;
  font-size: 12px;
  vertical-align: top;
}
.data-table code {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  word-break: break-all;
}
.row-bad {
  background: #fff5f5;
}
.row-skip {
  background: #f8fafc;
}
.modal-wide {
  width: 880px;
}
.modal-toolbar {
  display: flex;
  gap: 12px;
  margin-bottom: 10px;
}
.modal-tip {
  margin: 0;
  font-size: 12px;
  color: var(--muted);
  background: #f8fafc;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px 10px;
}
.modal-check {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 13px;
}
</style>
