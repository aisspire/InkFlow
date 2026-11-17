// Requires the isolated fixture and an existing Playwright/Chromium installation.
const { chromium } = require(process.env.INKFLOW_PLAYWRIGHT_MODULE || '../../.inkflow/browser-tools/node_modules/playwright');
const fs = require('fs'), path = require('path'), assert = require('assert');
const info = JSON.parse(fs.readFileSync(process.env.INKFLOW_UI_FIXTURE || '.inkflow/ui-verify/fixture.json', 'utf8'));
assert(info.isolated_ui_fixture === true, 'Use only tests/ui_workbench_fixture.py');
const base = `http://127.0.0.1:${info.port}`, out = process.env.INKFLOW_UI_EVIDENCE || '.inkflow/ui-verify/', errors = [], evidence = {};
let browser, page;
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn) { for (let i = 0; i < 120; i++) {
    const v = await fn();
    if (v)
        return v;
    await delay(150);
} throw Error('timeout'); }
async function api(method, url, data) { const r = await fetch(base + '/api' + url, { method, headers: { 'Content-Type': 'application/json', 'X-InkFlow-Document': info.doc.document_id }, body: data ? JSON.stringify(data) : undefined }); const v = await r.json(); assert(r.ok, JSON.stringify(v)); return v; }
async function nav(name) { await page.getByRole('navigation', { name: '全局导航' }).getByRole('link', { name, exact: true }).click(); }
async function select(label, text) { await page.getByLabel(label, { exact: true }).click(); await page.getByRole('option').filter({ hasText: text }).first().click(); }
async function settleVerification(ai, expected) { for (let i = 0; i < 180; i++) {
    const proposal = new URL(page.url()).searchParams.get('proposal'), v = await api('GET', '/ai/proposals/' + proposal);
    if (v.verification?.status !== 'running') {
        assert.equal(v.verification.status, expected);
        await ai.getByText('验证结果：' + (expected === 'passed' ? '通过' : '失败'), { exact: true }).waitFor();
        return;
    }
    const approve = ai.getByRole('button', { name: '确认候选与属性，采用到工作副本', exact: true });
    if (await approve.count()) {
        await ai.getByLabel('标题', { exact: true }).fill('Simulation');
        await ai.getByLabel('slug', { exact: true }).fill('simulation');
        await ai.getByLabel('日期 YYYY-MM-DD', { exact: true }).fill('2026-11-16');
        await approve.click();
    }
    const output = ai.getByRole('button', { name: '确认模拟输出', exact: true });
    if (await output.count())
        await output.click();
    await delay(200);
} throw Error('verification timeout'); }
async function widthCheck(tag) { for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 });
    await delay(150);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, tag + ':page:' + width);
    const overflow = await page.locator('[data-page]:visible').evaluate(e => ({ scroll: e.scrollWidth, client: e.clientWidth }));
    assert(overflow.scroll <= overflow.client + 1, tag + ':content:' + width + JSON.stringify(overflow));
    if ([390, 1440].includes(width))
        await page.screenshot({ path: out + tag + '-' + width + '.png' });
} }
(async () => {
    fs.mkdirSync(out, { recursive: true });
    for (const row of await api('GET', '/run-center')) {
        if (row.editor_source?.document_id === info.doc.document_id && !['completed', 'failed', 'stopped', 'interrupted'].includes(row.status)) {
            const r = await api('GET', '/runs/' + row.id);
            await api('POST', '/runs/' + row.id + '/actions', { request_id: crypto.randomUUID(), pending_id: r.pending?.id, action: 'stop', payload: {} });
            await until(async () => ['completed', 'failed', 'stopped', 'interrupted'].includes((await api('GET', '/runs/' + row.id)).status));
        }
    }
    browser = await chromium.launch({ headless: true, executablePath: process.env.INKFLOW_BROWSER_EXECUTABLE || undefined });
    page = await browser.newPage({ viewport: { width: 1440, height: 960 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + `?page=articles&document=${info.doc.document_id}`);
    const editor = page.getByRole('textbox', { name: '文章原文' });
    await editor.waitFor();
    assert.equal(await page.getByRole('navigation', { name: '全局导航' }).getByRole('link').count(), 4);
    assert.equal(await page.getByRole('dialog').count(), 0);
    await page.getByRole('navigation', { name: '文章路径树', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: '切换工作区', exact: true }).count(), 0);
    assert.equal(await page.getByRole('button', { name: '修改默认保存目录', exact: true }).count(), 1);
    evidence.articleTreeInline = true;
    await editor.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.insertText('\nIA kept draft');
    await delay(800);
    const kept = (await api('GET', '/documents/' + info.doc.document_id)).text;
    await nav('工作流');
    await page.getByText(/启用版本 .*草稿 revision/).waitFor();
    const initialDraft = page.getByRole('button', { name: '从此版本创建草稿', exact: true });
    if (await initialDraft.isEnabled())
        await initialDraft.click();
    await select('选择节点（键盘可操作）', '全文检查');
    const prompt = page.getByLabel('消息 1 模板', { exact: true });
    await prompt.waitFor();
    await prompt.fill((await prompt.inputValue()) + '\nIA unsaved workflow');
    await nav('任务');
    await page.getByRole('button', { name: '持续关注', exact: true }).click();
    assert(new URL(page.url()).searchParams.get('tab') === 'watch');
    await page.reload();
    await page.getByRole('button', { name: '创建持续任务', exact: true }).waitFor();
    await page.goBack();
    assert.equal(new URL(page.url()).searchParams.get('tab') || 'runs', 'runs');
    await page.goForward();
    assert.equal(new URL(page.url()).searchParams.get('tab'), 'watch');
    await nav('文章');
    await editor.waitFor();
    assert((await editor.innerText()).includes('IA kept draft'));
    // Refresh resets undo stack as documented; start a new session edit to verify cross-page undo.
    const undoToken = 'IA undo ' + crypto.randomUUID();
    await editor.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.insertText('\n' + undoToken);
    await nav('设置');
    await page.getByRole('heading', { name: '共享发布配置', exact: true }).waitFor();
    await nav('文章');
    await editor.click();
    await page.keyboard.press('Control+z');
    assert(!(await editor.innerText()).includes(undoToken));
    await page.keyboard.press('Control+y');
    assert((await editor.innerText()).includes(undoToken));
    evidence.articleUndoRedo = true;
    // Save a new local flow draft, then retain it through all page and studio view switches.
    await nav('工作流');
    await page.getByText(/启用版本 .*草稿 revision/).waitFor();
    const create = page.getByRole('button', { name: '从此版本创建草稿', exact: true });
    if (await create.isEnabled())
        await create.click();
    await select('选择节点（键盘可操作）', '全文检查');
    await prompt.waitFor();
    await prompt.fill((await prompt.inputValue()) + '\nIA session draft');
    await page.getByRole('button', { name: '测试', exact: true }).click();
    await page.getByRole('button', { name: '版本', exact: true }).click();
    await nav('设置');
    await nav('工作流');
    await page.getByRole('button', { name: '设计', exact: true }).click();
    assert((await prompt.inputValue()).includes('IA session draft'));
    evidence.workflowDraftPreserved = true;
    await page.getByRole('button', { name: '保存流程草稿', exact: true }).click();
    await widthCheck('workflow');
    await nav('文章');
    await widthCheck('article');
    await nav('任务');
    await page.getByRole('button', { name: '运行记录', exact: true }).click();
    await widthCheck('tasks');
    // AI invalid capability, failed gate, passing gate, manual change invalidation.
    await nav('工作流');
    await page.getByRole('button', { name: 'AI 创建流程', exact: true }).click();
    const ai = page.getByRole('region', { name: 'AI 创建与修复' });
    await ai.getByLabel('用户目标', { exact: true }).fill('forbidden');
    await ai.getByRole('button', { name: '发送目标并调用 AI', exact: true }).click();
    await ai.getByRole('heading', { name: '无效草稿', exact: true }).waitFor();
    assert(await ai.getByRole('button', { name: '已核对差异，采用为草稿', exact: true }).isDisabled());
    evidence.capabilityGate = true;
    await ai.getByRole('button', { name: '1. 描述目标', exact: true }).click();
    await ai.getByLabel('用户目标', { exact: true }).fill('保留正文并结束');
    await ai.getByRole('button', { name: '发送目标并调用 AI', exact: true }).click();
    await ai.getByRole('heading', { name: '待审阅', exact: true }).waitFor();
    await ai.getByRole('button', { name: '已核对差异，采用为草稿', exact: true }).click();
    await ai.getByRole('button', { name: '下一步：样例测试', exact: true }).click();
    await ai.getByLabel('必须包含（每行一条）', { exact: true }).fill('MISSING');
    await ai.getByRole('button', { name: '校验并比较原／新版本模拟', exact: true }).click();
    await settleVerification(ai, 'failed');
    await ai.getByRole('button', { name: '下一步：启用', exact: true }).click();
    assert(await ai.getByRole('button', { name: '明确启用已验证版本', exact: true }).isDisabled());
    evidence.failedGate = true;
    await ai.getByRole('button', { name: '3. 样例测试', exact: true }).click();
    await ai.getByLabel('必须包含（每行一条）', { exact: true }).fill('示例');
    await ai.getByRole('button', { name: '校验并比较原／新版本模拟', exact: true }).click();
    await settleVerification(ai, 'passed');
    await ai.getByRole('button', { name: '下一步：启用', exact: true }).click();
    assert(await ai.getByRole('button', { name: '明确启用已验证版本', exact: true }).isEnabled());
    await ai.getByRole('button', { name: '明确启用已验证版本', exact: true }).click();
    await ai.getByRole('heading', { name: '已启用', exact: true }).waitFor();
    evidence.aiEnabled = true;
    await widthCheck('ai');
    const proposal = new URL(page.url()).searchParams.get('proposal');
    await page.reload();
    await page.getByRole('region', { name: 'AI 创建与修复' }).getByRole('heading', { name: '已启用', exact: true }).waitFor();
    assert(new URL(page.url()).searchParams.get('proposal') === proposal);
    evidence.aiRefresh = true;
    await ai.getByRole('button', { name: '返回来源页面', exact: true }).click();
    assert(!new URL(page.url()).searchParams.get('ai'));
    assert.equal(await page.getByRole('dialog').count(), 0);
    // A real declarative article待办 cannot expose its editor-dependent confirm in task center.
    await nav('设置');
    await page.getByLabel('配置名称', { exact: true }).fill('IA temporary blog');
    await page.getByLabel('博客仓库目录', { exact: true }).fill(info.root);
    await page.getByLabel('仓库内内容目录', { exact: true }).fill('ia-output');
    await page.getByRole('button', { name: '保存共享配置修订', exact: true }).click();
    await page.getByText('共享配置修订已保存。已有流程继续使用原固定修订。', { exact: true }).waitFor();
    const profiles = await api('GET', '/publish-profiles'), profile = profiles.find(p => p.payload.name === 'IA temporary blog');
    assert(profile);
    evidence.sharedProfiles = true;
    const catalog = await api('POST', '/workflow-definitions', { name: 'IA publishing' }), wid = catalog.draft.payload.workflow_id;
    catalog.draft.payload.definition.nodes.find(n => n.node_type_id === 'confirm-local-output').config = { profile_id: profile.profile_id, revision: profile.revision };
    let c = await api('PUT', `/workflow-assets/${wid}/draft`, { revision: catalog.draft.revision, package: catalog.draft.payload });
    c = await api('POST', `/workflow-assets/${wid}/activate`, { revision: c.draft.revision, generation: c.active.generation });
    const doc = await api('GET', '/documents/' + info.doc.document_id);
    let run = await api('POST', '/runs', { request_id: crypto.randomUUID(), workflow_id: wid, raw_text: doc.text, editor_source: { document_id: doc.document_id, revision: doc.revision } });
    run = await until(async () => { const r = await api('GET', '/runs/' + run.id); return r.status === 'waiting' ? r : false; });
    await page.goto(base + `?page=tasks&tab=runs&run=${run.id}&runDocument=${doc.document_id}&document=${doc.document_id}`);
    await page.getByRole('button', { name: '前往文章审阅', exact: true }).waitFor();
    await page.reload();
    await page.getByRole('button', { name: '前往文章审阅', exact: true }).waitFor();
    assert.equal(new URL(page.url()).searchParams.get('run'), run.id);
    evidence.selectedRunRefresh = true;
    assert.equal(await page.getByRole('button', { name: '确认候选与属性，采用到工作副本', exact: true }).count(), 0);
    await page.getByRole('button', { name: '前往文章审阅', exact: true }).click();
    await page.getByRole('button', { name: '确认候选与属性，采用到工作副本', exact: true }).waitFor();
    await page.getByLabel('标题', { exact: true }).fill('IA reviewed');
    await page.getByLabel('slug', { exact: true }).fill('ia-reviewed-' + Date.now());
    await page.getByLabel('日期 YYYY-MM-DD', { exact: true }).fill('2026-11-16');
    await page.getByRole('button', { name: '确认候选与属性，采用到工作副本', exact: true }).click();
    await page.getByRole('button', { name: '确认写入本地文件', exact: true }).waitFor();
    await page.getByRole('button', { name: '确认写入本地文件', exact: true }).click();
    await until(async () => { const r = await api('GET', '/runs/' + run.id); return r.status === 'completed'; });
    evidence.declarativeOutput = true;
    assert.equal(fs.readFileSync(path.join(info.root, 'browser.mdx'), 'utf8'), '# P6 Browser\r\n\r\n<X />\r\n');
    evidence.sourceUnchanged = true;
    await page.getByRole('button', { name: '返回来源页面', exact: true }).click();
    assert(new URL(page.url()).searchParams.get('page') === 'tasks');
    evidence.reviewReturn = true;
    await page.keyboard.press('Tab');
    assert(await page.evaluate(() => document.activeElement.tagName !== 'BODY'));
    await nav('设置');
    await widthCheck('settings');
    assert.deepEqual(errors, []);
    evidence.pageErrors = errors;
    fs.writeFileSync(out + 'browser-evidence.json', JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
    await browser.close();
})().catch(async (e) => { console.error(e); if (page) {
    console.error(await page.locator('body').innerText().catch(() => ''));
    await page.screenshot({ path: out + 'failure.png' }).catch(() => { });
} if (browser)
    await browser.close(); process.exitCode = 1; });
