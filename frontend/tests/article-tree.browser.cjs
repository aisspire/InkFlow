// Uses only the isolated fixture and existing Playwright/Chromium installation.
const { chromium } = require(process.env.INKFLOW_PLAYWRIGHT_MODULE || '../../.inkflow/browser-tools/node_modules/playwright');
const fs = require('fs'), path = require('path'), assert = require('assert');
const info = JSON.parse(fs.readFileSync(process.env.INKFLOW_UI_FIXTURE || '.inkflow/ui-verify/fixture.json', 'utf8'));
assert(info.isolated_ui_fixture === true, 'Use only tests/ui_workbench_fixture.py');
const base = `http://127.0.0.1:${info.port}`, out = process.env.INKFLOW_UI_EVIDENCE || '.inkflow/ui-verify/';
const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
async function api(method, url, data) {
  const response = await fetch(base + '/api' + url, { method, headers: { 'Content-Type': 'application/json' }, body: data ? JSON.stringify(data) : undefined });
  const value = await response.json(); assert(response.ok, JSON.stringify(value)); return value;
}
async function until(check) {
  for (let i = 0; i < 100; i++) { if (await check()) return; await delay(100); }
  throw new Error('Timed out waiting for article state');
}
let browser;
(async () => {
  const folder = path.join(info.root, 'article-tree-' + Date.now());
  const first = path.join(folder, 'alpha', 'same.md'), second = path.join(folder, 'beta', 'same.md');
  const defaultFolder = path.join(folder, 'new-articles');
  for (const directory of [path.dirname(first), path.dirname(second), defaultFolder]) fs.mkdirSync(directory, { recursive: true });
  fs.writeFileSync(first, '# Alpha\n'); fs.writeFileSync(second, '# Beta\n');
  fs.writeFileSync(path.join(folder, 'never-opened.md'), '# Not in sidebar\n');
  const a = await api('POST', '/documents/open', { path: first }), b = await api('POST', '/documents/open', { path: second });
  const spaces = await api('GET', '/workspaces');
  fs.mkdirSync(out, { recursive: true });
  browser = await chromium.launch({ headless: true, executablePath: process.env.INKFLOW_BROWSER_EXECUTABLE || undefined });
  const page = await browser.newPage({ viewport: { width: 1440, height: 960 } }), errors = [], scans = [];
  await page.addInitScript(id => {
    if (localStorage.getItem('article-tree-fixture-seeded')) return;
    localStorage.setItem('inkflow-open:documents', JSON.stringify({ ids: [id], current: id }));
    localStorage.setItem('article-tree-fixture-seeded', '1');
  }, a.document_id);
  // Simulate an old browser with one open entry and another previously closed article.
  await page.route('**/api/documents', route => route.request().method() === 'GET' ? route.fulfill({ contentType: 'application/json', body: JSON.stringify([a, b].map(d => ({ ...d, draft: false }))) }) : route.continue());
  page.on('pageerror', error => errors.push(error.message));
  page.on('request', request => { if (/\/api\/workspaces\/[^/]+\/files/.test(request.url())) scans.push(request.url()); });
  await page.goto(base + `?page=articles&document=${a.document_id}`);
  const tree = page.getByRole('navigation', { name: '文章路径树', exact: true });
  const editor = page.getByRole('textbox', { name: '文章原文' });
  const activeId = () => page.evaluate(() => new URLSearchParams(location.search).get('document'));
  await editor.waitFor(); await tree.waitFor();
  assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 2);
  await page.unroute('**/api/documents');
  // Compact file rows expose full location and state on hover and keyboard focus.
  const alphaButton = tree.locator(`button[data-document-id=${JSON.stringify(a.document_id)}]`);
  await alphaButton.hover();
  const fileTooltip = page.getByRole('tooltip').filter({ hasText: a.location });
  await fileTooltip.waitFor();
  assert((await fileTooltip.innerText()).includes('已保存到文件'));
  await delay(250);
  await page.screenshot({ path: path.join(out, 'file-details-hover-1440.png') });
  await page.mouse.move(900, 20); await fileTooltip.waitFor({ state: 'hidden' });
  await page.keyboard.press('Tab'); await alphaButton.focus();
  await fileTooltip.waitFor();
  await page.keyboard.press('Tab'); await page.mouse.move(900, 20);
  assert.equal(await tree.getByText('never-opened.md', { exact: true }).count(), 0);
  assert.equal(await page.getByRole('button', { name: '切换工作区', exact: true }).count(), 0);

  // Changing a default directory validates a path, never registers/scans a workspace or switches the article.
  await editor.click(); await page.keyboard.press('Control+End'); await page.keyboard.insertText('draft kept');
  await page.getByRole('button', { name: '修改默认保存目录', exact: true }).click();
  let dialog = page.getByRole('dialog', { name: '默认保存目录', exact: true });
  await dialog.getByRole('textbox', { name: '默认保存目录', exact: true }).fill(path.join(folder, 'absent'));
  await dialog.getByRole('button', { name: '使用此目录', exact: true }).click();
  await dialog.getByRole('alert').waitFor();
  await dialog.getByRole('textbox', { name: '默认保存目录', exact: true }).fill(defaultFolder);
  await dialog.getByRole('button', { name: '使用此目录', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await activeId(), a.document_id); assert((await editor.innerText()).includes('draft kept'));
  assert.deepEqual(await api('GET', '/workspaces'), spaces);
  await editor.click(); await page.keyboard.press('Control+z'); assert(!(await editor.innerText()).includes('draft kept'));
  await page.keyboard.press('Control+y'); assert((await editor.innerText()).includes('draft kept'));
  await until(async () => (await api('GET', '/documents/' + a.document_id)).text.includes('draft kept'));

  // Removing an inactive entry survives reload; recent articles explicitly restore it.
  const betaButton = tree.locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`);
  await betaButton.locator('..').getByRole('button', { name: '从侧栏移除 same.md', exact: true }).click();
  await page.reload(); await editor.waitFor();
  assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 1);
  assert(fs.existsSync(second)); assert.equal((await api('GET', '/documents/' + b.document_id)).text, '# Beta\n');
  await page.getByText('最近文章与移除的草稿', { exact: true }).click();
  await page.locator('.recent-article-list').locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).click();
  await until(async () => await activeId() === b.document_id);
  assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 2);

  // Recent file cleanup survives reload and preserves disk content, draft and identity.
  await tree.locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).locator('..').getByRole('button', { name: '从侧栏移除 same.md', exact: true }).click();
  await until(async () => await activeId() === a.document_id);
  await page.locator('.recent-article-list').locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).locator('..').getByRole('button', { name: '清除最近记录 same.md', exact: true }).click();
  await page.reload(); await editor.waitFor();
  await page.getByText('最近文章与移除的草稿', { exact: true }).click();
  assert.equal(await page.locator('.recent-article-list').locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).count(), 0);
  assert.equal((await api('GET', '/documents/' + b.document_id)).text, '# Beta\n');
  assert.equal(fs.readFileSync(second, 'utf8'), '# Beta\n');
  await page.locator('.sidebar-file-actions').getByRole('button', { name: '打开文章', exact: true }).click();
  let openDialog = page.getByRole('dialog', { name: '打开文章', exact: true });
  await openDialog.getByRole('textbox', { name: '文件完整路径', exact: true }).fill(b.location);
  await openDialog.getByRole('button', { name: '打开此文件', exact: true }).click();
  await openDialog.waitFor({ state: 'hidden' });
  await until(async () => await activeId() === b.document_id);
  assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 2);

  // Delete removed unnamed drafts only after confirmation; failures keep the row and text.
  await page.locator('.sidebar-file-actions').getByRole('button', { name: '新建文章', exact: true }).click();
  await until(async () => (await activeId()) !== b.document_id);
  const deletedId = await activeId(), deletedName = '未命名文章 · ' + deletedId.slice(0, 6);
  await editor.click(); await page.keyboard.insertText('# Disposable draft');
  await until(async () => (await api('GET', '/documents/' + deletedId)).text === '# Disposable draft');
  await tree.getByRole('button', { name: '从侧栏移除 ' + deletedName, exact: true }).click();
  const recent = page.locator('.recent-article-list');
  if (!await recent.evaluate(el => el.open)) await recent.locator('summary').click();
  await recent.getByRole('button', { name: '删除草稿 ' + deletedName, exact: true }).click();
  let deleteDialog = page.getByRole('dialog', { name: '删除未命名草稿？', exact: true });
  await deleteDialog.getByRole('button', { name: '取消', exact: true }).click();
  assert.equal((await api('GET', '/documents/' + deletedId)).text, '# Disposable draft');
  await page.route(`**/api/documents/${deletedId}`, route => route.request().method() === 'DELETE' ? route.fulfill({ status: 409, contentType: 'application/json', body: JSON.stringify({ detail: 'isolated deletion conflict' }) }) : route.continue());
  await recent.getByRole('button', { name: '删除草稿 ' + deletedName, exact: true }).click();
  await deleteDialog.getByRole('button', { name: '删除草稿', exact: true }).click();
  await deleteDialog.getByRole('alert').getByText('isolated deletion conflict', { exact: true }).waitFor();
  assert.equal((await api('GET', '/documents/' + deletedId)).text, '# Disposable draft');
  await page.unroute(`**/api/documents/${deletedId}`);
  await deleteDialog.getByRole('button', { name: '删除草稿', exact: true }).click();
  await deleteDialog.waitFor({ state: 'hidden' });
  assert.equal((await fetch(base + '/api/documents/' + deletedId)).status, 404);
  await page.reload(); await editor.waitFor();
  assert(!(await api('GET', '/documents')).some(d => d.document_id === deletedId));
  assert.equal(await tree.getByRole('button', { name: '从侧栏移除 ' + deletedName, exact: true }).count(), 0);
  await tree.locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).click();
  await until(async () => await activeId() === b.document_id);

  // A failed replacement load must leave the active entry in the sidebar.
  await page.route(`**/api/documents/${a.document_id}`, route => route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'isolated switch failure' }) }));
  await tree.locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).locator('..').getByRole('button', { name: '从侧栏移除 same.md', exact: true }).click();
  await page.getByText('isolated switch failure', { exact: true }).waitFor();
  assert.equal(await activeId(), b.document_id); assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 2);
  await page.unroute(`**/api/documents/${a.document_id}`);
  await tree.locator(`button[data-document-id=${JSON.stringify(a.document_id)}]`).click();
  await until(async () => await activeId() === a.document_id);
  assert((await editor.innerText()).includes('draft kept'));

  // New documents remain unnamed until first explicit save; first save preserves identity.
  await page.locator('.sidebar-file-actions').getByRole('button', { name: '新建文章', exact: true }).click();
  await until(async () => (await activeId()) !== a.document_id);
  const draftId = await activeId();
  await editor.click(); await page.keyboard.insertText('# New draft\n');
  await until(async () => (await api('GET', '/documents/' + draftId)).text === '# New draft\n');
  assert.deepEqual(fs.readdirSync(defaultFolder), []);
  await tree.getByRole('region', { name: '未命名草稿', exact: true }).waitFor();
  await page.getByRole('button', { name: '保存文件', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '首次保存到文件', exact: true });
  assert.equal((await dialog.getByRole('textbox', { name: '目标目录', exact: true }).inputValue()).toLowerCase(), defaultFolder.toLowerCase());
  await dialog.getByRole('textbox', { name: '文件名（Markdown / MDX）', exact: true }).fill('new.md');
  await dialog.getByRole('button', { name: '保存到此位置', exact: true }).click();
  await dialog.waitFor({ state: 'hidden' });
  assert.equal(await activeId(), draftId); assert.equal(fs.readFileSync(path.join(defaultFolder, 'new.md'), 'utf8'), '# New draft\n');
  assert.equal(await tree.getByRole('region', { name: '未命名草稿', exact: true }).count(), 0);

  // Save-as adds a new identity and leaves the original article accessible.
  await page.locator('.article-more summary').click();
  await page.locator('.article-more').getByRole('button', { name: '另存为', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '另存为新文件', exact: true });
  await dialog.getByRole('textbox', { name: '文件名（Markdown / MDX）', exact: true }).fill('copy.md');
  await dialog.getByRole('button', { name: '保存到此位置', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
  const copyId = await activeId(); assert.notEqual(copyId, draftId);
  assert.equal(await tree.getByRole('button', { name: /^new\.md/ }).count(), 1);
  assert.equal(await tree.getByRole('button', { name: /^copy\.md/ }).count(), 1);

  // Refresh notices an external move; explicit relinking preserves document ID and draft.
  const moved = path.join(defaultFolder, 'moved.md'); fs.renameSync(path.join(defaultFolder, 'copy.md'), moved);
  await page.getByRole('button', { name: '刷新文章状态', exact: true }).click();
  await page.getByRole('button', { name: '重新定位文件', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '重新定位文件', exact: true });
  await dialog.getByRole('textbox', { name: '文件现在的完整路径', exact: true }).fill(moved);
  await dialog.getByRole('button', { name: '关联此文件', exact: true }).click(); await dialog.waitFor({ state: 'hidden' });
  assert.equal(await activeId(), copyId); assert.equal(await tree.getByRole('button', { name: /^moved\.md/ }).count(), 1);
  assert.equal(await tree.getByRole('button', { name: /^copy\.md/ }).count(), 0);

  // One search filters the whole path tree; clear restores it. Directory keys support keyboard collapse.
  const search = page.getByRole('searchbox', { name: '搜索文章', exact: true });
  await search.fill('alpha'); assert.equal(await tree.getByRole('button', { name: /^same\.md/ }).count(), 1);
  await search.fill('nothing-matches'); await tree.getByText('没有匹配文章。', { exact: true }).waitFor(); await search.fill('');
  const directory = tree.locator('.directory-toggle').first();
  await directory.focus(); await page.keyboard.press('ArrowLeft'); assert.equal(await directory.getAttribute('aria-expanded'), 'false');
  await page.keyboard.press('ArrowRight'); assert.equal(await directory.getAttribute('aria-expanded'), 'true');

  await page.reload(); await editor.waitFor(); assert.equal(await activeId(), copyId);
  assert.equal(await page.evaluate(() => localStorage.getItem('inkflow-default-directory')), (await api('POST', '/local-directory', { path: defaultFolder })).path);
  const retainedDraft = await api('POST', '/documents', {});
  await tree.locator(`button[data-document-id=${JSON.stringify(b.document_id)}]`).locator('..').getByRole('button', { name: '从侧栏移除 same.md', exact: true }).click();
  await page.getByRole('button', { name: '刷新文章状态', exact: true }).click();
  if (!await recent.evaluate(el => el.open)) await recent.locator('summary').click();
  await recent.getByRole('button', { name: '删除草稿 未命名文章 · ' + retainedDraft.document_id.slice(0, 6), exact: true }).waitFor();
  for (const width of [320, 390, 768, 1440]) {
    await page.setViewportSize({ width, height: 960 }); await delay(150);
    if (width <= 900) await page.getByRole('button', { name: '打开文件导航', exact: true }).click();
    await tree.waitFor();
    if (width <= 900) await until(() => page.locator('.files-drawer').evaluate(el => Math.abs(el.getBoundingClientRect().left) < 1));
    const visibleRecent = page.locator('.article-files:visible .recent-article-list');
    if (!await visibleRecent.evaluate(el => el.open)) await visibleRecent.locator('summary').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false, 'page overflow ' + width);
    const bounds = await page.locator('.article-files:visible').evaluate(el => ({ scroll: el.scrollWidth, client: el.clientWidth }));
    assert(bounds.scroll <= bounds.client + 1, 'sidebar overflow ' + width);
    const rowHeights = await page.locator('.article-files:visible .opened-article').evaluateAll(rows => rows.filter(el => el.getClientRects().length).map(el => el.getBoundingClientRect().height));
    assert(rowHeights.every(height => height <= (width <= 900 ? 40 : 32) + 1), 'compact row height ' + width + ': ' + JSON.stringify(rowHeights));
    assert.equal(await page.locator('.article-files:visible .opened-article small').count(), 0);
    if ([390, 1440].includes(width)) await page.screenshot({ path: path.join(out, 'article-tree-' + width + '.png') });
    if (width === 390) {
      await recent.getByRole('button', { name: '删除草稿 未命名文章 · ' + retainedDraft.document_id.slice(0, 6), exact: true }).click();
      await deleteDialog.waitFor();
      await delay(300); // Wait for the MUI dialog fade before capturing its stable appearance.
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth), false);
      await page.screenshot({ path: path.join(out, 'delete-draft-390.png') });
      await deleteDialog.getByRole('button', { name: '取消', exact: true }).click();
    }
    if (width <= 900) { await page.getByRole('button', { name: '收起文件导航', exact: true }).click(); await tree.waitFor({ state: 'hidden' }); }
  }
  assert.deepEqual(scans, []); assert.deepEqual(errors, []);
  fs.writeFileSync(path.join(out, 'article-tree-evidence.json'), JSON.stringify({ pathTree: true, compactRows: { desktop: 32, narrow: 40 }, fileDetailsOnHoverAndFocus: true, noDirectoryScanning: true, defaultDirectory: true, removalAndRecovery: true, recentCleanupAndReopen: true, draftDeletionCancelConflictAndReload: true, failedRemovalProtected: true, unnamedFirstSave: true, saveAs: true, relocation: true, searchAndKeyboard: true, responsive: [320, 390, 768, 1440], pageErrors: errors }, null, 2));
  await browser.close(); browser = undefined;
  console.log('Article path tree browser checks passed.');
})().catch(async error => { console.error(error); if (browser) await browser.close(); process.exitCode = 1; });
