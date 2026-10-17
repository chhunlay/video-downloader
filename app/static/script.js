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
    video: '<rect x="3" y="4" width="18" height="16" rx="3"/><path d="m10 9 5 3-5 3z"/>',
    audio: '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    tiktok: '<rect x="6" y="2" width="12" height="20" rx="2.5"/><path d="M11 18h2"/>',
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
let previewIsTikTok = false;
let previewResolutions = [];  // [[height, sizeStr | null], ...], highest first
let audioQuality = '320';  // the highest quality is picked unless changed
let subtitlesEnabled = false;
let subMode = 'srt';

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
    setHidden(['videoInfo', 'errorMessage', 'progressCard', 'failedItems'], true);
    previewOk = false;
    updateActions();
}

// Options and buttons show once there is something to download
function updateActions() {
    const ready = mode === 'batch' ? countLinks() > 0 : previewOk;
    $('actions').classList.toggle('hidden', !ready);
}

const isTikTok = url => /tiktok\.com/i.test(url);

// Buttons fit the link: a single TikTok link downloads without the watermark
// from the Video button (a note says so); the separate TikTok button is only for batches
function showDownloadButtons() {
    $('tiktokNote').classList.toggle('hidden', !(mode === 'single' && previewIsTikTok));
    setHidden(['videoBtn', 'audioBtn'], false);
    $('tiktokBtn').classList.toggle('hidden', mode !== 'batch');
}

// A picker (resolution or audio quality) replaces the Video/Audio/TikTok buttons while open
const PICKERS = ['resolutionPicker', 'audioPicker'];

function openPicker(id) {
    PICKERS.forEach(p => $(p).classList.toggle('hidden', p !== id));
    setHidden(['videoBtn', 'audioBtn', 'tiktokBtn', 'tiktokNote'], true);
}

function closePickers() {
    setHidden(PICKERS, true);
    showDownloadButtons();
}

function setMode(newMode) {
    mode = newMode;
    $('singleInput').classList.toggle('hidden', mode === 'batch');
    $('batchInput').classList.toggle('hidden', mode === 'single');
    // Trim and subtitles only make sense for one known video, so batch has no options
    $('optionsToggle').classList.toggle('hidden', mode === 'batch');
    if (mode === 'batch') setOptionsOpen(false);
    $('subtitleOptions').classList.toggle('hidden', mode === 'batch' || $('subLang').options.length === 0);
    closePickers();
    hideResults();
    if (mode === 'single' && $('url').value) $('url').dispatchEvent(new Event('input'));
    (mode === 'batch' ? $('urls') : $('url')).focus();
}

// Browsers restore old input on refresh/back, so start clean every time
function resetForm() {
    $('url').value = '';
    $('urls').value = '';
    updateLinkCount();
    updateInputButtons();
    hideResults();
    setFetching(false);
}

// ---------- Clear ----------
function updateInputButtons() {
    const hasText = $('url').value.length > 0;
    $('clearBtn').classList.toggle('hidden', !hasText);
}

function clearLink() {
    $('url').value = '';
    $('url').dispatchEvent(new Event('input'));
    $('url').focus();
}

// ---------- Options ----------
function setOptionsOpen(open) {
    $('optionsPanel').classList.toggle('hidden', !open);
    $('optionsChevron').classList.toggle('rotate-180', open);
    $('optionsToggle').setAttribute('aria-expanded', String(open));
}

function toggleOptions() {
    setOptionsOpen($('optionsPanel').classList.contains('hidden'));
}

// One line telling what the closed Options panel will do, e.g. "0:30–1:15 · Khmer .srt"
function updateOptionsSummary() {
    const start = $('trimStart').value.trim();
    const end = $('trimEnd').value.trim();
    const trim = start || end ? `${start || '0:00'}–${end || 'end'}` : 'Full video';
    let subs = 'No subtitles';
    const lang = $('subLang').selectedOptions[0];
    if (subtitlesEnabled && lang) {
        subs = `${lang.textContent.replace(/ \(auto.*\)$/, '')} ${subMode === 'burn' ? 'burned in' : '.srt'}`;
    }
    $('optionsSummary').textContent = `${trim} · ${subs}`;
}
['trimStart', 'trimEnd'].forEach(id => $(id).addEventListener('input', updateOptionsSummary));
$('subLang').addEventListener('change', updateOptionsSummary);

function bindChips(containerId, onSelect) {
    $(containerId).addEventListener('click', e => {
        const chip = e.target.closest('.chip');
        if (!chip) return;
        $(containerId).querySelectorAll('.chip').forEach(c => c.classList.toggle('selected', c === chip));
        onSelect(chip.dataset.value);
    });
}
bindChips('subModeChips', value => { subMode = value; updateOptionsSummary(); });

function setSubtitlesEnabled(enabled) {
    subtitlesEnabled = enabled;
    $('subToggle').classList.toggle('on', enabled);
    $('subToggle').setAttribute('aria-checked', String(enabled));
    $('subDetails').classList.toggle('hidden', !enabled);
    updateOptionsSummary();
}

function renderSubtitles(languages) {
    setSubtitlesEnabled(false);  // every new video starts without subtitles
    const select = $('subLang');
    select.replaceChildren(...(languages || []).map(lang => {
        const option = document.createElement('option');
        option.value = lang.code;
        option.textContent = lang.name + (lang.translated ? ' (auto-translated)' : lang.auto ? ' (auto)' : '');
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

function formatDuration(seconds) {
    const s = Math.round(seconds);
    const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = String(s % 60).padStart(2, '0');
    return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}

// Landscape fills the width; portrait (TikTok, Shorts) is capped in height and centered
const PORTRAIT_MAX_HEIGHT = 320;

function showThumbnail(data) {
    const w = data.width || 16, h = data.height || 9;
    const wrap = $('thumbWrap');
    wrap.style.aspectRatio = `${w} / ${h}`;
    wrap.style.width = h > w ? `${Math.round(PORTRAIT_MAX_HEIGHT * w / h)}px` : '100%';
    // No picture: leave the dark box instead of a broken-image icon
    $('thumbnail').classList.toggle('hidden', !data.thumbnail);
    $('thumbnail').src = data.thumbnail || '';
    $('thumbnail').alt = data.title ? `Thumbnail: ${data.title}` : 'Video thumbnail';

    const badge = $('durationBadge');
    badge.classList.toggle('hidden', !data.duration);
    if (data.duration) badge.textContent = formatDuration(data.duration);
    $('trimEnd').placeholder = data.duration ? `End (max ${formatDuration(data.duration)})` : 'End (e.g. 1:30)';
}

$('url').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const url = $('url').value;
    updateInputButtons();

    hideResults();
    if (!url) return;

    debounceTimer = setTimeout(() => {
        setFetching(true);

        postForm("/info", {url})
        .then(res => res.json())
        .then(data => {
            setFetching(false);

            if (data.error) {
                showError(data.error);
                return;
            }

            $('videoInfo').classList.remove('hidden');
            showThumbnail(data);
            $('videoTitle').innerText = data.title;
            $('videoUploader').innerText = data.uploader ? "By " + data.uploader : '';

            previewOk = true;
            previewTitle = data.title || '';
            previewIsTikTok = isTikTok(url);
            $('trimStart').value = '';
            $('trimEnd').value = '';
            previewResolutions = data.resolutions || [];
            closePickers();
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
    if (mode === 'single' && previewIsTikTok) {
        startDownload('tiktok');
        return;
    }
    const resolutions = mode === 'batch' ? BATCH_RESOLUTIONS : previewResolutions;
    if (resolutions.length === 0) {
        $('resolutionSelect').value = '';
        startDownload('video');
        return;
    }
    $('resolutionLabel').textContent = mode === 'batch' ? 'Max resolution' : 'Resolution';
    renderResolutions(resolutions, mode === 'single');
    openPicker('resolutionPicker');
}

function selectResolution(value) {
    $('resolutionSelect').value = value;
    document.querySelectorAll('#resolutionCards .res-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.value === value);
    });
}

function selectAudioQuality(value) {
    audioQuality = value;
    document.querySelectorAll('#audioCards .res-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.value === value);
    });
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
let activeJobId = null;

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

// ---------- Progress details ----------
function formatBytes(bytes) {
    const units = ['KB', 'MB', 'GB'];
    let value = bytes / 1024, unit = 0;
    while (value >= 1024 && unit < units.length - 1) {
        value /= 1024;
        unit++;
    }
    return `${value < 10 && unit ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

// "38 MB of 120 MB · 4.2 MB/s · 0:35 left"; unknown parts are left out
function progressDetail(job) {
    if (job.status !== 'downloading' || !job.downloaded_bytes) return '';
    const parts = [job.total_bytes
        ? `${formatBytes(job.downloaded_bytes)} of ${formatBytes(job.total_bytes)}`
        : formatBytes(job.downloaded_bytes)];
    if (job.speed) parts.push(`${formatBytes(job.speed)}/s`);
    if (job.eta != null && job.speed) parts.push(`${formatDuration(job.eta)} left`);
    return parts.join(' · ');
}

function showProgress(job) {
    const finished = job.status === 'done';
    const failed = job.status === 'error' || job.status === 'interrupted';
    const sizeUnknown = job.status === 'downloading' && !job.percent && !job.total_bytes;
    $('barTrack').classList.toggle('progress-indeterminate', sizeUnknown);
    $('bar').style.width = job.percent + '%';
    $('percentLabel').innerText = sizeUnknown ? '' : job.percent + '%';

    const detail = progressDetail(job);
    $('progressDetail').textContent = detail;
    $('progressDetail').classList.toggle('hidden', !detail);
    $('statusIcon').innerHTML = finished ? icon('check') : failed ? icon('x') : '';
    $('statusIcon').className = 'shrink-0 ' + (finished ? 'text-emerald-400' : failed ? 'text-rose-400' : '');

    let text = STATUS_LABELS[job.status] || job.status;
    if (finished && job.saved_locally) text = 'Saved to Downloads';
    if (failed && job.error) text += ': ' + job.error;
    if (finished && job.failed_items.length) text += ` · ${job.failed_items.length} skipped`;
    $('status').textContent = text;
    $('status').title = text;

    $('cancelBtn').classList.toggle('hidden', finished || failed);

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

// Deleting a running job stops it on the server and frees its files
async function cancelDownload() {
    if (!activeJobId) return;
    const jobId = activeJobId;
    activeJobId = null;
    if (activeSource) activeSource.close();
    $('cancelBtn').classList.add('hidden');
    $('statusIcon').innerHTML = icon('x');
    $('statusIcon').className = 'shrink-0 text-muted';
    $('status').textContent = 'Cancelled';
    $('barTrack').classList.remove('progress-indeterminate');
    $('progressDetail').classList.add('hidden');
    try { await fetch(`/jobs/${jobId}`, {method: 'DELETE'}); } catch (e) {}
    removeFromHistory(jobId);
}

function trackJob(jobId) {
    if (activeSource) activeSource.close();
    const evt = new EventSource(`/progress/${jobId}`);
    activeSource = evt;
    activeJobId = jobId;

    evt.onmessage = e => {
        const job = JSON.parse(e.data);
        showProgress(job);

        // Jobs from 0.10.0 already put their files in ~/Downloads, so only the rest are saved here
        if (job.status === 'done' && !job.saved_locally) {
            evt.close();
            saveJobFiles(job);
        }
        if (job.status === 'done' || job.status === 'error' || job.status === 'interrupted') {
            evt.close();
            activeJobId = null;
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
        if (subtitlesEnabled && dtype !== 'audio' && $('subLang').value) {
            fields.sub_lang = $('subLang').value;
            fields.sub_mode = subMode;
        }
    }

    setHidden(['errorMessage'], true);
    $('progressCard').classList.remove('hidden');
    showProgress({status: 'queued', percent: 0, kind: mode, item_index: 0, item_count: 1, failed_items: []});
    $('cancelBtn').classList.add('hidden');  // nothing to cancel until the server has a job

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

    closePickers();
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
        if (job && job.status === 'done' && job.saved_locally) state = 'Saved to Downloads';
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
            const local = job && job.saved_locally;
            if (job && job.status === 'done' && !local) {
                buttons.append(historyButton('Save', 'primary', () => saveJobFiles(job)));
            }
            // Removing a local download only forgets it; the file stays in ~/Downloads
            buttons.append(historyButton(local ? 'Remove' : 'Delete', '', () => deleteJob(entry.id)));
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
