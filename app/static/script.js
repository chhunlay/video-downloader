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

// ---------- Theme ----------
const THEME_KEY = 'video-downloader-theme';
const html = document.documentElement;

function applyTheme(theme) {
    const isLight = theme === 'light';
    html.setAttribute('data-theme', isLight ? 'light' : 'dark');
    $('themeIcon').textContent = isLight ? '☀️' : '🌙';
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
function setFetching(isLoading) {
    $('url').classList.toggle('is-loading', isLoading);
    $('loadingSpinner').classList.toggle('visible', isLoading);
    $('fetchingLabel').classList.toggle('visible', isLoading);
}

function hideResults() {
    setHidden(['videoInfo', 'errorMessage', 'progressCard', 'downloadLink'], true);
}

// The resolution picker replaces the Video/Audio/TikTok buttons while open
function setPickerOpen(open) {
    $('resolutionPicker').classList.toggle('hidden', !open);
    setHidden(['videoBtn', 'audioBtn', 'tiktokBtn'], open);
}

// Browsers restore old input on refresh/back, so start clean every time
function resetForm() {
    $('url').value = '';
    hideResults();
    setFetching(false);
}
resetForm();
window.addEventListener('pageshow', resetForm);

// ---------- Video info lookup ----------
let debounceTimer;
let currentResolutions = [];  // [[height, sizeStr | null], ...], highest first

function renderResolutions(resolutions) {
    currentResolutions = resolutions || [];
    if (currentResolutions.length === 0) {
        $('resolutionSelect').value = '';  // don't reuse the previous video's choice
        return;
    }

    // The highest resolution is selected by default
    $('resolutionSelect').value = currentResolutions[0][0];
    $('resolutionCards').innerHTML = currentResolutions.map(([h, size], i) =>
        `<button type="button" class="res-card${i === 0 ? ' selected' : ''}" data-value="${h}" onclick="selectResolution('${h}')">` +
            `<span class="res-card-label">${h}p</span>` +
            (size ? `<span class="res-card-size">${size}</span>` : '') +
        `</button>`
    ).join('');

    // One option, or nothing above 480p, usually means the site is limiting this app
    const maxRes = Math.max(...currentResolutions.map(([h]) => h));
    $('resolutionLimitedNote').classList.toggle('hidden', !(currentResolutions.length <= 1 || maxRes < 480));
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
                $('errorMessage').classList.remove('hidden');
                return;
            }

            $('videoInfo').classList.remove('hidden');
            $('thumbnail').src = data.thumbnail;
            $('videoTitle').innerText = data.title;
            $('videoUploader').innerText = "By " + data.uploader;

            setPickerOpen(false);
            renderResolutions(data.resolutions);
        })
        .catch(err => {
            setFetching(false);
            $('errorMessage').classList.remove('hidden');
            console.error(err);
        });
    }, 500);
});

// ---------- Resolution picker ----------
// "Video" opens the picker, or downloads at best quality when there's nothing to pick
function handleVideoClick() {
    if (currentResolutions.length > 0) {
        setPickerOpen(true);
    } else {
        startDownload('video');
    }
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
function saveFile(filename) {
    // An off-DOM link starts the browser download; the server deletes the file after serving it once
    const tempLink = document.createElement('a');
    tempLink.href = "/downloads/" + encodeURIComponent(filename);
    tempLink.download = filename;
    document.body.appendChild(tempLink);
    tempLink.click();
    tempLink.remove();
}

function startDownload(dtype) {
    const statusText = $('status');
    const percentLabel = $('percentLabel');
    const bar = $('bar');

    $('progressCard').classList.remove('hidden');
    setHidden(['downloadLink', 'downloadHint'], true);
    bar.style.width = '0%';
    percentLabel.innerText = '0%';
    statusText.innerText = 'Status: Starting…';

    // Only video downloads use a resolution
    const resolution = dtype === 'video' ? $('resolutionSelect').value : '';

    postForm("/download", {url: $('url').value, type: dtype, resolution});

    const evt = new EventSource("/progress");
    evt.onmessage = e => {
        const data = JSON.parse(e.data);
        bar.style.width = data.percent + '%';
        percentLabel.innerText = data.percent + '%';
        statusText.innerText = `Status: ${data.status}`;

        if (data.status === "done") {
            evt.close();
            saveFile(data.filename);
            statusText.innerText = "✅ Downloaded";
        }

        if (data.status.startsWith("error")) {
            evt.close();
            statusText.innerText = "❌ " + data.status;
        }
    };
}
