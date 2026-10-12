// ---------- Helpers ----------
const $ = id => document.getElementById(id);

function setHidden(ids, hidden) {
    ids.forEach(id => $(id).classList.toggle('hidden', hidden));
}

function postForm(path, fields) {
    return fetch(path, {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: new URLSearchParams(fields)
    });
}

const SVG_ATTRS = 'xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"';
const ICON_PATHS = {
    check: '<path d="m5 12 5 5 9-10"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    video: '<path d="M12 4v12"/><path d="m6 10 6 6 6-6"/><path d="M5 20h14"/>',
    audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    tiktok: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 9 5 3-5 3z"/>',
    batch: '<path d="M4 7h16M4 12h16M4 17h10"/>',
};

function icon(name, size = 'h-4 w-4') {
    return `<svg class="${size}" ${SVG_ATTRS}>${ICON_PATHS[name]}</svg>`;
}

// ---------- Theme ----------
const THEME_KEY = 'video-downloader-theme';
const html = document.documentElement;

function applyTheme(theme) {
    const isLight = theme === 'light';
    html.setAttribute('data-theme', isLight ? 'light' : 'dark');
    $('themeIconSun').classList.toggle('hidden', !isLight);
    $('themeIconMoon').classList.toggle('hidden', isLight);
}

function getStoredTheme() {
    try {
        return localStorage.getItem(THEME_KEY);
    } catch (e) {
        return null;
    }
}

const systemPrefersLight = window.matchMedia && window.matchMedia('(prefers-color-scheme: light)').matches;
applyTheme(getStoredTheme() || (systemPrefersLight ? 'light' : 'dark'));

$('themeToggle').addEventListener('click', () => {
    const next = html.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
});

// ---------- Form state ----------
let mode = 'single';
let previewOk = false;
let previewTitle = '';
let previewResolutions = [];  // [[height, sizeStr | null], ...], highest first
let audioQuality = '192';
let subMode = 'off';

const BATCH_LIMIT = 50;
// Batch videos differ, so these act as a maximum height
const BATCH_RESOLUTIONS = [[1080, null], [720, null], [480, null], [360, null]];

function setFetching(isLoading) {
    $('url').classList.toggle('is-loading', isLoading);
    $('loadingSpinner').classList.toggle('visible', isLoading);
    $('fetchingLabel').classList.toggle('visible', isLoading);
}

function showError(message) {
    $('errorText').textContent = message;
    $('errorMessage').classList.remove('hidden');
}

function hideResults() {
    setHidden(['videoInfo', 'errorMessage', 'progressCard', 'saveAgain', 'failedItems'], true);
    previewOk = false;
    updateActions();
}

// Options and buttons show once there is something to download
function updateActions() {
    const ready = mode === 'batch' ? countLinks() > 0 : previewOk;
    $('actions').classList.toggle('hidden', !ready);
}

// The resolution picker replaces the Video/Audio/TikTok buttons while open
function setPickerOpen(open) {
    $('resolutionPicker').classList.toggle('hidden', !open);
    setHidden(['videoBtn', 'audioBtn', 'tiktokBtn'], open);
}

function setMode(newMode) {
    mode = newMode;
    $('modeSingle').classList.toggle('active', mode === 'single');
    $('modeBatch').classList.toggle('active', mode === 'batch');
    $('singleInput').classList.toggle('hidden', mode === 'batch');
    $('batchInput').classList.toggle('hidden', mode === 'single');
    // Trim and subtitles only make sense for one known video
    $('trimOptions').classList.toggle('hidden', mode === 'batch');
    $('subtitleOptions').classList.toggle('hidden', mode === 'batch' || $('subLang').options.length === 0);
    setPickerOpen(false);
    hideResults();
    if (mode === 'single' && $('url').value) $('url').dispatchEvent(new Event('input'));
}

// Browsers restore old input on refresh/back, so start clean every time
function resetForm() {
    $('url').value = '';
    $('urls').value = '';
    updateLinkCount();
    hideResults();
    setFetching(false);
}

// ---------- Options ----------
function toggleOptions() {
    const open = $('optionsPanel').classList.toggle('hidden') === false;
    $('optionsChevron').classList.toggle('rotate-180', open);
}

function bindChips(containerId, onSelect) {
    $(containerId).addEventListener('click', e => {
        const chip = e.target.closest('.chip');
        if (!chip) return;
        $(containerId).querySelectorAll('.chip').forEach(c => c.classList.toggle('selected', c === chip));
        onSelect(chip.dataset.value);
    });
}
bindChips('qualityChips', value => { audioQuality = value; });
bindChips('subModeChips', value => { subMode = value; });

function renderSubtitles(languages) {
    const select = $('subLang');
    select.replaceChildren(...(languages || []).map(lang => {
        const option = document.createElement('option');
        option.value = lang.code;
        option.textContent = lang.name + (lang.auto ? ' (auto)' : '');
        return option;
    }));
    $('subtitleOptions').classList.toggle('hidden', select.options.length === 0 || mode === 'batch');
}

// ---------- Batch links ----------
function countLinks() {
    const words = $('urls').value.split(/\s+/).filter(w => /^https?:\/\//i.test(w));
    return new Set(words).size;
}

function updateLinkCount() {
    const n = countLinks();
    $('linkCount').textContent = `${n} link${n === 1 ? '' : 's'} (max ${BATCH_LIMIT})`;
    $('linkCount').classList.toggle('text-rose-400', n > BATCH_LIMIT);
}

$('urls').addEventListener('input', () => {
    setHidden(['errorMessage'], true);
    updateLinkCount();
    updateActions();
});

// ---------- Video info lookup ----------
let debounceTimer;

function renderResolutions(resolutions, limitedNote) {
    if (resolutions.length === 0) {
        $('resolutionSelect').value = '';  // don't reuse the previous video's choice
        return;
    }

    // The highest resolution is selected by default
    $('resolutionSelect').value = resolutions[0][0];
    $('resolutionCards').innerHTML = resolutions.map(([h, size], i) =>
        `<button type="button" class="res-card${i === 0 ? ' selected' : ''}" data-value="${h}" onclick="selectResolution('${h}')">` +
            `<span class="res-card-label">${h}p</span>` +
            (size ? `<span class="res-card-size">${size}</span>` : '') +
        `</button>`
    ).join('');

    // One option, or nothing above 480p, usually means the site is limiting this app
    const maxRes = Math.max(...resolutions.map(([h]) => h));
    $('resolutionLimitedNote').classList.toggle('hidden', !limitedNote || !(resolutions.length <= 1 || maxRes < 480));
}

$('url').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const url = $('url').value;

    hideResults();
    if (!url) return;

    debounceTimer = setTimeout(() => {
        setFetching(true);

        postForm("/info", {url})
        .then(res => res.json())
        .then(data => {
            setFetching(false);

            if (data.error) {
                showError('Video not found or invalid URL');
                return;
            }

            $('videoInfo').classList.remove('hidden');
            $('thumbnail').src = data.thumbnail;
            $('videoTitle').innerText = data.title;
            $('videoUploader').innerText = "By " + data.uploader;

            previewOk = true;
            previewTitle = data.title || '';
            previewResolutions = data.resolutions || [];
            setPickerOpen(false);
            renderSubtitles(data.subtitles);
            updateActions();
        })
        .catch(err => {
            setFetching(false);
            showError('Video not found or invalid URL');
            console.error(err);
        });
    }, 500);
});

// ---------- Resolution picker ----------
// "Video" opens the picker, or downloads at best quality when there's nothing to pick
function handleVideoClick() {
    const resolutions = mode === 'batch' ? BATCH_RESOLUTIONS : previewResolutions;
    if (resolutions.length === 0) {
        $('resolutionSelect').value = '';
        startDownload('video');
        return;
    }
    $('resolutionLabel').textContent = mode === 'batch' ? 'Max resolution' : 'Resolution';
    renderResolutions(resolutions, mode === 'single');
    setPickerOpen(true);
}

function selectResolution(value) {
    $('resolutionSelect').value = value;
    document.querySelectorAll('#resolutionCards .res-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.value === value);
    });
}

function hideResolutionPicker() {
    setPickerOpen(false);
}

// ---------- Download ----------
const STATUS_LABELS = {
    queued: 'Starting…',
    downloading: 'Downloading',
    processing: 'Processing',
    optimizing: 'Optimizing for social media',
    zipping: 'Zipping',
    done: 'Downloaded',
    error: 'Failed',
    interrupted: 'Interrupted',
};
let activeSource = null;

function saveJobFiles(job) {
    // Off-DOM links start the browser downloads; spaced out so browsers allow several
    job.files.forEach((name, i) => setTimeout(() => {
        const link = document.createElement('a');
        link.href = `/files/${job.id}/${encodeURIComponent(name)}`;
        link.download = name;
        document.body.appendChild(link);
        link.click();
        link.remove();
    }, i * 600));
}

function showProgress(job) {
    const finished = job.status === 'done';
    const failed = job.status === 'error' || job.status === 'interrupted';
    $('bar').style.width = job.percent + '%';
    $('percentLabel').innerText = job.percent + '%';
    $('statusIcon').innerHTML = finished ? icon('check') : failed ? icon('x') : '';
    $('statusIcon').className = 'shrink-0 ' + (finished ? 'text-emerald-400' : failed ? 'text-rose-400' : '');

    let text = STATUS_LABELS[job.status] || job.status;
    if (failed && job.error) text += ': ' + job.error;
    if (finished && job.failed_items.length) text += ` · ${job.failed_items.length} skipped`;
    $('status').textContent = text;
    $('status').title = text;

    const showCounter = job.kind === 'batch' && !finished && !failed;
    $('batchCounter').classList.toggle('hidden', !showCounter);
    $('batchCounter').textContent = `${job.item_index + 1} / ${job.item_count}`;

    $('failedItems').classList.toggle('hidden', job.failed_items.length === 0);
    $('failedItems').replaceChildren(...job.failed_items.map(item => {
        const li = document.createElement('li');
        li.textContent = `${item.url} — ${item.error}`;
        li.className = 'truncate';
        li.title = li.textContent;
        return li;
    }));
}

function trackJob(jobId) {
    if (activeSource) activeSource.close();
    const evt = new EventSource(`/progress/${jobId}`);
    activeSource = evt;

    evt.onmessage = e => {
        const job = JSON.parse(e.data);
        showProgress(job);

        if (job.status === 'done') {
            evt.close();
            saveJobFiles(job);
            $('saveAgain').onclick = () => saveJobFiles(job);
            $('saveAgain').classList.remove('hidden');
        }
        if (job.status === 'done' || job.status === 'error' || job.status === 'interrupted') {
            evt.close();
            refreshHistory();
        }
    };
    evt.onerror = () => evt.close();
}

async function startDownload(dtype) {
    const fields = {
        mode,
        type: dtype,
        audio_quality: audioQuality,
        // Only video downloads use a resolution
        resolution: dtype === 'video' ? $('resolutionSelect').value : '',
    };
    if (mode === 'batch') {
        fields.urls = $('urls').value;
    } else {
        fields.url = $('url').value;
        fields.trim_start = $('trimStart').value;
        fields.trim_end = $('trimEnd').value;
        if (subMode !== 'off' && dtype !== 'audio' && $('subLang').value) {
            fields.sub_lang = $('subLang').value;
            fields.sub_mode = subMode;
        }
    }

    setHidden(['errorMessage', 'saveAgain'], true);
    $('progressCard').classList.remove('hidden');
    showProgress({status: 'queued', percent: 0, kind: mode, item_index: 0, item_count: 1, failed_items: []});

    let response, data;
    try {
        response = await postForm("/download", fields);
        data = await response.json();
    } catch (err) {
        $('progressCard').classList.add('hidden');
        showError('Could not reach the server');
        return;
    }
    if (!response.ok) {
        $('progressCard').classList.add('hidden');
        showError(data.error || 'Could not start the download');
        return;
    }

    setPickerOpen(false);
    addToHistory({
        id: data.job_id,
        title: mode === 'batch' ? '' : previewTitle,
        dtype,
        kind: mode,
        created: Date.now(),
    });
    refreshHistory();
    trackJob(data.job_id);
}

// ---------- History (this browser only) ----------
const HISTORY_KEY = 'video-downloader-history';
const HISTORY_LIMIT = 50;
let historyJobs = {};  // id -> job fields from the server, or null when expired

function loadHistory() {
    try {
        const list = JSON.parse(localStorage.getItem(HISTORY_KEY));
        return Array.isArray(list) ? list : [];
    } catch (e) {
        return [];
    }
}

function saveHistory(list) {
    try { localStorage.setItem(HISTORY_KEY, JSON.stringify(list.slice(0, HISTORY_LIMIT))); } catch (e) {}
}

function addToHistory(entry) {
    saveHistory([entry, ...loadHistory().filter(e => e.id !== entry.id)]);
}

function removeFromHistory(id) {
    saveHistory(loadHistory().filter(e => e.id !== id));
    delete historyJobs[id];
    renderHistory();
}

async function deleteJob(id) {
    try { await fetch(`/jobs/${id}`, {method: 'DELETE'}); } catch (e) {}
    removeFromHistory(id);
}

function relativeTime(ms) {
    const minutes = Math.floor((Date.now() - ms) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${minutes} min ago`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours} h ago`;
    return new Date(ms).toLocaleDateString();
}

function historyButton(label, className, onClick) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.className = `history-btn ${className}`;
    button.addEventListener('click', onClick);
    return button;
}

function renderHistory() {
    const entries = loadHistory();
    $('historySection').classList.toggle('hidden', entries.length === 0);

    $('historyList').replaceChildren(...entries.map(entry => {
        const job = historyJobs[entry.id];
        const expired = job === null;
        const li = document.createElement('li');
        li.className = 'history-row flex items-center gap-3 rounded-xl px-3 py-2' + (expired ? ' opacity-50' : '');

        const iconBox = document.createElement('span');
        iconBox.className = 'shrink-0 text-muted';
        iconBox.innerHTML = icon(entry.kind === 'batch' ? 'batch' : entry.dtype);

        const text = document.createElement('div');
        text.className = 'min-w-0 flex-1';
        const title = document.createElement('p');
        title.className = 'text-sm font-medium truncate';
        title.textContent = (entry.kind === 'batch' ? job && job.title : entry.title) || (job && job.title) || 'Download';
        title.title = title.textContent;
        const meta = document.createElement('p');
        meta.className = 'text-xs text-faint';
        let state = expired ? 'Expired' : job ? STATUS_LABELS[job.status] : '';
        if (job && job.status === 'error' && job.error) state += ': ' + job.error;
        meta.textContent = [relativeTime(entry.created), state].filter(Boolean).join(' · ');
        meta.title = meta.textContent;
        meta.classList.add('truncate');
        text.append(title, meta);

        const buttons = document.createElement('div');
        buttons.className = 'flex gap-1.5 shrink-0';
        if (expired) {
            buttons.append(historyButton('Remove', '', () => removeFromHistory(entry.id)));
        } else {
            if (job && job.status === 'done') {
                buttons.append(historyButton('Save', 'primary', () => saveJobFiles(job)));
            }
            buttons.append(historyButton('Delete', '', () => deleteJob(entry.id)));
        }

        li.append(iconBox, text, buttons);
        return li;
    }));
}

async function refreshHistory() {
    const entries = loadHistory();
    if (entries.length) {
        try {
            const response = await fetch('/jobs/lookup', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ids: entries.map(e => e.id)}),
            });
            if (response.ok) historyJobs = (await response.json()).jobs;
        } catch (e) {}
    }
    renderHistory();
}

// ---------- Start ----------
resetForm();
window.addEventListener('pageshow', resetForm);
refreshHistory();
