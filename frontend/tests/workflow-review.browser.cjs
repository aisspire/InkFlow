// Requires the isolated fixture and an existing Playwright/Chromium installation.
const { chromium } = require(process.env.INKFLOW_PLAYWRIGHT_MODULE || '../../.inkflow/browser-tools/node_modules/playwright');
const fs = require('fs'), assert = require('assert'), path = require('path');
const info = JSON.parse(fs.readFileSync(process.env.INKFLOW_UI_FIXTURE || '.inkflow/ui-verify/fixture.json'));
assert(info.isolated_ui_fixture === true, 'Use only tests/ui_workbench_fixture.py');
const base = 'http://127.0.0.1:' + info.port, out = process.env.INKFLOW_UI_EVIDENCE || '.inkflow/ui-verify/';
let browser, page;
const evidence = {}, errors = [];
const delay = ms => new Promise(r => setTimeout(r, ms));
async function until(fn) { for (let i = 0; i < 200; i++) {
    const v = await fn();
    if (v)
        return v;
    await delay(150);
} throw Error('timeout'); }
;
async function api(method, url, data) { const r = await fetch(base + '/api' + url, { method, headers: { 'Content-Type': 'application/json', 'X-InkFlow-Document': info.doc.document_id }, body: data ? JSON.stringify(data) : undefined }); const v = await r.json(); assert(r.ok, JSON.stringify(v)); return v; }
async function select(label, text) { await page.getByLabel(label, { exact: true }).click(); await page.getByRole('option').filter({ hasText: text }).first().click(); }
async function runStart(wid) { const d = await api('GET', '/documents/' + info.doc.document_id); const r = await api('POST', '/runs', { request_id: crypto.randomUUID(), workflow_id: wid, raw_text: d.text, no_publish: true, editor_source: { document_id: d.document_id, revision: d.revision } }); return await until(async () => { fs.mkdirSync(out, { recursive: true }); const v = await api('GET', '/runs/' + r.id); return v.status === 'waiting' ? v : false; }); }
async function waitKind(id, kind) { return until(async () => { const r = await api('GET', '/runs/' + id); return r.pending?.kind === kind ? r : false; }); }
(async () => {
    browser = await chromium.launch({ headless: true, executablePath: process.env.INKFLOW_BROWSER_EXECUTABLE || undefined });
    page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
    page.on('pageerror', e => errors.push(e.message));
    await page.goto(base + `?page=tasks&run=${info.run}&runDocument=${info.doc.document_id}`);
    await page.locator('[data-page=tasks] .react-flow__node[data-id=full_check]').click();
    await page.getByRole('button', { name: '技术详情与诊断', exact: true }).click();
    await page.getByRole('button', { name: 'AI 分析并提出修复', exact: true }).first().click();
    const ai = page.getByRole('region', { name: 'AI 创建与修复' });
    await ai.getByRole('button', { name: '发送目标并调用 AI', exact: true }).click();
    await ai.getByRole('heading', { name: '待审阅', exact: true }).waitFor();
    await ai.getByRole('button', { name: '已核对差异，采用为草稿', exact: true }).click();
    await ai.getByRole('button', { name: '打开此流程设计器', exact: true }).click();
    await page.getByText(/启用版本 .*草稿 revision/).waitFor();
    await page.locator('.flow-settings summary').click();
    await page.getByLabel('流程说明', { exact: true }).fill('IA human edited definition');
    await page.getByRole('button', { name: '保存流程草稿', exact: true }).click();
    await page.getByText('流程草稿已保存。新运行仍使用已启用版本。', { exact: true }).waitFor();
    await page.getByRole('button', { name: '继续 AI 审阅', exact: true }).click();
    await ai.getByText(/人工修改后，验证已失效/).waitFor();
    await ai.getByRole('button', { name: '3. 样例测试', exact: true }).click();
    assert(await ai.getByRole('button', { name: '校验并比较原／新版本模拟', exact: true }).isDisabled());
    evidence.manualInvalidation = true;
    await ai.getByRole('button', { name: '2. 审阅方案', exact: true }).click();
    await ai.getByRole('button', { name: '采用人工保存的草稿并重置验证', exact: true }).click();
    await ai.getByRole('button', { name: '3. 样例测试', exact: true }).click();
    await ai.getByLabel('允许验证中的模型调用（可能计费）', { exact: true }).check();
    await ai.getByRole('button', { name: '校验并比较原／新版本模拟', exact: true }).click();
    await ai.getByRole('button', { name: '结束当前检查', exact: true }).waitFor();
    await ai.getByRole('button', { name: '结束当前检查', exact: true }).click();
    await ai.getByText('验证结果：通过', { exact: true }).waitFor();
    await ai.getByRole('button', { name: '4. 启用', exact: true }).click();
    await ai.getByRole('button', { name: '明确启用已验证版本', exact: true }).click();
    await ai.getByRole('heading', { name: '已启用', exact: true }).waitFor();
    await ai.getByRole('button', { name: '以当前已保存工作副本创建关联修复运行', exact: true }).click();
    await ai.getByText(/关联修复运行已创建/).waitFor();
    const pid = new URL(page.url()).searchParams.get('proposal');
    const repaired = await until(async () => { for (const row of await api('GET', '/run-center')) {
        const r = await api('GET', '/runs/' + row.id);
        if (r.repair_of?.proposal_id === pid && r.status === 'waiting')
            return r;
    } return false; });
    evidence.repairRun = repaired.id;
    assert.equal(repaired.repair_of.run_id, info.run);
    await page.goto(base + `?page=tasks&run=${repaired.id}&runDocument=${info.doc.document_id}`);
    await page.getByRole('button', { name: '前往文章审阅', exact: true }).waitFor();
    assert.equal(await page.getByRole('button', { name: /^结束(?:当前|本轮)检查$/ }).count(), 0);
    await page.getByRole('button', { name: '前往文章审阅', exact: true }).click();
    await page.getByRole('button', { name: /^结束(?:当前|本轮)检查$/ }).click();
    await until(async () => (await api('GET', '/runs/' + repaired.id)).status === 'completed');
    evidence.repairReview = true;
    // All three original builtin definitions retain their own service protocol.
    for (const wid of ['full-check', 'redaction-only', 'reviewed-publishing']) {
        let c = await api('GET', '/workflow-assets/' + wid);
        if (!c.draft)
            c = await api('POST', '/workflow-assets/' + wid + '/draft', { version_id: null });
        const p = c.draft.payload;
        p.model.enabled = wid !== 'reviewed-publishing';
        if (wid === 'redaction-only') {
            p.prompts['risk-check'].messages[0].content += '\nINKFLOW_UI_RISK';
            p.prompts['redaction-rewrite'].messages[0].content += '\nINKFLOW_UI_REWRITE';
            const currentDoc = await api('GET', '/documents/' + info.doc.document_id);
            await api('POST', '/documents/' + currentDoc.document_id + '/operation', { action: 'edit', revision: currentDoc.revision, text: currentDoc.text + '\nUI_SECRET' });
        }
        if (wid === 'full-check')
            for (const prompt of Object.values(p.prompts))
                prompt.messages[0].content += '\nP6 corrected';
        for (const groups of Object.values(p.node_config))
            for (const [group, fields] of Object.entries(groups))
                for (const key of Object.keys(fields))
                    if (key === 'dir')
                        fields[key] = path.join(info.root, 'ia-' + group);
        c = await api('PUT', '/workflow-assets/' + wid + '/draft', { revision: c.draft.revision, package: p });
        await api('POST', '/workflow-assets/' + wid + '/activate', { revision: c.draft.revision, generation: c.active.generation });
        const r = await runStart(wid);
        await page.goto(base + `?page=articles&document=${info.doc.document_id}&articleRun=${r.id}`);
        if (wid === 'full-check') {
            await page.getByRole('button', { name: /^结束(?:当前|本轮)检查$/ }).click();
        }
        else if (wid === 'redaction-only') {
            await page.getByRole('button', { name: '确认审核（全部忽略则结束）', exact: true }).click();
            await page.getByRole('button', { name: '接受改动并复查', exact: true }).click();
            await page.getByRole('button', { name: '确认结束本轮脱敏', exact: true }).click();
            const adopted = await api('GET', '/documents/' + info.doc.document_id);
            assert(!adopted.text.includes('UI_SECRET'));
            assert(adopted.text.includes('公开样例'));
            evidence.redactionCandidateAdopted = true;
        }
        else {
            await page.getByRole('button', { name: '确认审核并继续', exact: true }).click();
            await waitKind(r.id, 'final_review');
            await page.getByRole('button', { name: '对比并采用候选', exact: true }).click();
            assert.equal(await page.getByRole('dialog').count(), 0);
            await page.getByRole('button', { name: '采用候选到工作副本', exact: true }).click();
            const editor = page.getByRole('textbox', { name: '文章原文' });
            await editor.click();
            await page.keyboard.press('Control+End');
            await page.keyboard.insertText('\nLegacy main editor body');
            await page.getByRole('button', { name: '文章属性', exact: true }).click();
            assert(await page.getByRole('button', { name: '提交属性为新审阅版本', exact: true }).isDisabled());
            evidence.unsubmittedPropertyGuard = true;
            await page.getByRole('button', { name: '提交编辑稿到工作流', exact: true }).click();
            await until(async () => { const v = await api('GET', '/runs/' + r.id); return v.pending?.kind === 'final_review' && v.pending.revision !== r.pending.revision && v.pending.document.includes('Legacy main editor body'); });
            await page.getByRole('button', { name: '文章属性', exact: true }).click();
            await page.getByLabel('标题', { exact: true }).fill('Legacy metadata');
            await page.getByRole('button', { name: '提交属性为新审阅版本', exact: true }).click();
            await until(async () => { const v = await api('GET', '/runs/' + r.id); return v.pending?.article?.title === 'Legacy metadata'; });
            // API completion can precede the article's SSE projection; compare the visible version.
            await page.getByText('Legacy metadata', { exact: true }).waitFor();
            await page.getByRole('button', { name: '对比并采用候选', exact: true }).click();
            await page.getByRole('button', { name: '采用候选到工作副本', exact: true }).click();
            await page.getByRole('button', { name: '保存审阅稿并结束', exact: true }).click();
        }
        await until(async () => (await api('GET', '/runs/' + r.id)).status === 'completed');
        evidence[wid] = true;
    }
    assert.equal(fs.readFileSync(path.join(info.root, 'browser.mdx'), 'utf8'), '# P6 Browser\r\n\r\n<X />\r\n');
    evidence.sourceUnchanged = true;
    // Keep selection and scroll through page navigation without remounting the editor.
    const editor = page.getByRole('textbox', { name: '文章原文' });
    await editor.click();
    await page.keyboard.press('Control+End');
    await page.keyboard.insertText('\n' + Array.from({ length: 160 }, (_, i) => 'scroll ' + i).join('\n'));
    await delay(800);
    await page.keyboard.down('Shift');
    for (let i = 0; i < 10; i++) await page.keyboard.press('ArrowLeft');
    await page.keyboard.up('Shift');
    const beforeSelection = (await api('GET', '/documents/' + info.doc.document_id)).text;
    await page.locator('.cm-scroller').evaluate(e => e.scrollTop = 500);
    const before = await page.locator('.cm-scroller').evaluate(e => e.scrollTop);
    await page.getByRole('navigation', { name: '全局导航' }).getByRole('link', { name: '设置', exact: true }).click();
    await page.getByRole('navigation', { name: '全局导航' }).getByRole('link', { name: '文章', exact: true }).click();
    assert(Math.abs((await page.locator('.cm-scroller').evaluate(e => e.scrollTop)) - before) < 3);
    evidence.editorScroll = true;
    await editor.focus();
    await page.keyboard.insertText('CURSOR_PRESERVED');
    await delay(800);
    assert.equal((await api('GET', '/documents/' + info.doc.document_id)).text, beforeSelection.slice(0, -10) + 'CURSOR_PRESERVED');
    evidence.editorSelection = true;
    await page.screenshot({ path: out + 'legacy-review-1440.png' });
    await page.setViewportSize({ width: 390, height: 950 });
    await page.screenshot({ path: out + 'legacy-review-390.png' });
    assert.deepEqual(errors, []);
    evidence.pageErrors = errors;
    fs.writeFileSync(out + 'chains-evidence.json', JSON.stringify(evidence, null, 2));
    console.log(JSON.stringify(evidence));
    await browser.close();
})().catch(async (e) => { console.error(e); if (page) {
    console.error(await page.locator('body').innerText().catch(() => ''));
    await page.screenshot({ path: out + 'chains-failure.png' }).catch(() => { });
} if (browser)
    await browser.close(); process.exitCode = 1; });
