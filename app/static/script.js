// ---------- Theme ----------
const THEME_KEY = 'video-downloader-theme';
const html = document.documentElement;
const themeIcon = document.getElementById('themeIcon');

function applyTheme(theme) {
    if (theme === 'light') {
        html.setAttribute('data-theme', 'light');
        themeIcon.textContent = '☀️';
    } else {
        html.setAttribute('data-theme', 'dark');
        themeIcon.textContent = '🌙';
    }
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

document.getElementById('themeToggle').addEventListener('click', () => {
    const next = html.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
    applyTheme(next);
    try { localStorage.setItem(THEME_KEY, next); } catch (e) {}
});

// ---------- Reset stale input on load/refresh ----------
// Browsers restore form field values (and bfcache pages) on refresh/back,
// which left the URL box and old preview showing after a reload. Force a
// clean slate every time the page becomes visible.
function setFetching(isLoading) {
    document.getElementById('url').classList.toggle('is-loading', isLoading);
    document.getElementById('loadingSpinner').classList.toggle('visible', isLoading);
    document.getElementById('fetchingLabel').classList.toggle('visible', isLoading);
}

function resetForm() {
    const urlInput = document.getElementById('url');
    urlInput.value = '';
    document.getElementById('videoInfo').classList.add('hidden');
    document.getElementById('errorMessage').classList.add('hidden');
    document.getElementById('progressCard').classList.add('hidden');
    document.getElementById('downloadLink').classList.add('hidden');
    setFetching(false);
}
resetForm();
window.addEventListener('pageshow', resetForm);

// ---------- Video info lookup ----------
let debounceTimer;
let currentResolutions = [];

document.getElementById('url').addEventListener('input', () => {
    clearTimeout(debounceTimer);
    const url = document.getElementById('url').value;

    document.getElementById('videoInfo').classList.add('hidden');
    document.getElementById('errorMessage').classList.add('hidden');
    document.getElementById('progressCard').classList.add('hidden');
    document.getElementById('downloadLink').classList.add('hidden');

    if (!url) return;

    debounceTimer = setTimeout(() => {
        setFetching(true);

        fetch("/info", {
            method: "POST",
            headers: {"Content-Type": "application/x-www-form-urlencoded"},
            body: `url=${encodeURIComponent(url)}`
        })
        .then(res => res.json())
        .then(data => {
            setFetching(false);

            if (!data.error) {
                document.getElementById('videoInfo').classList.remove('hidden');
                document.getElementById('thumbnail').src = data.thumbnail;
                document.getElementById('videoTitle').innerText = data.title;
                document.getElementById('videoUploader').innerText = "By " + data.uploader;

                const resolutionSelect = document.getElementById('resolutionSelect');
                const resolutionCards = document.getElementById('resolutionCards');
                const resolutionLimitedNote = document.getElementById('resolutionLimitedNote');

                // Picker (and the Audio/TikTok buttons it hides while
                // open) reset to their default state for every fresh
                // video - the picker only reveals when "Video" is
                // actually clicked (handleVideoClick). This just
                // prepares the picker's contents ahead of time.
                document.getElementById('resolutionPicker').classList.add('hidden');
                document.getElementById('videoBtn').classList.remove('hidden');
                document.getElementById('audioBtn').classList.remove('hidden');
                document.getElementById('tiktokBtn').classList.remove('hidden');
                // Each entry is [height, sizeStr|null] - the picker shows
                // both, but the resolution actually submitted is just the
                // height (selectResolution's `value`).
                currentResolutions = data.resolutions || [];

                if (currentResolutions.length > 0) {
                    // No standalone "Best" card - resolutions arrive
                    // highest-first, so that first entry is selected by
                    // default and amounts to the same thing.
                    resolutionSelect.value = currentResolutions[0][0];
                    resolutionCards.innerHTML = currentResolutions.map(([h, size], i) =>
                        `<button type="button" class="res-card${i === 0 ? ' selected' : ''}" data-value="${h}" onclick="selectResolution('${h}')">` +
                            `<span class="res-card-label">${h}p</span>` +
                            (size ? `<span class="res-card-size">${size}</span>` : '') +
                        `</button>`
                    ).join('');

                    // A real, unrestricted video normally has several
                    // resolutions up to 720p/1080p+. Seeing only one or
                    // two options capped low is the signature of a site
                    // (most often YouTube) currently rate-limiting or
                    // bot-checking this app - not a picker bug.
                    const maxRes = Math.max(...currentResolutions.map(([h]) => h));
                    resolutionLimitedNote.classList.toggle('hidden', !(currentResolutions.length <= 1 || maxRes < 480));
                }
            } else {
                document.getElementById('errorMessage').classList.remove('hidden');
            }
        })
        .catch(err => {
            setFetching(false);
            document.getElementById('errorMessage').classList.remove('hidden');
            console.error(err);
        });
    }, 500);
});

// "Video" button: if this video actually has resolution choices to
// make, reveal the picker (+ its own "Download" button) instead of
// downloading immediately, hiding Audio/TikTok so it reads as a
// focused "pick a resolution" step rather than one more option among
// several. If there's nothing to pick (site exposed no format info at
// all), just download at best quality right away.
function handleVideoClick() {
    if (currentResolutions.length > 0) {
        document.getElementById('resolutionPicker').classList.remove('hidden');
        document.getElementById('videoBtn').classList.add('hidden');
        document.getElementById('audioBtn').classList.add('hidden');
        document.getElementById('tiktokBtn').classList.add('hidden');
    } else {
        startDownload('video');
    }
}

// Cards mirror a radio group: clicking one stores its value in the
// hidden input (read by startDownload exactly like the old <select>
// was) and toggles the "selected" style on/off across the set.
function selectResolution(value) {
    document.getElementById('resolutionSelect').value = value;
    document.querySelectorAll('#resolutionCards .res-card').forEach(card => {
        card.classList.toggle('selected', card.dataset.value === value);
    });
}

function hideResolutionPicker() {
    document.getElementById('resolutionPicker').classList.add('hidden');
    document.getElementById('videoBtn').classList.remove('hidden');
    document.getElementById('audioBtn').classList.remove('hidden');
    document.getElementById('tiktokBtn').classList.remove('hidden');
}

function startDownload(dtype) {
    const urlInput = document.getElementById('url');
    const progressCard = document.getElementById('progressCard');
    const statusText = document.getElementById('status');
    const percentLabel = document.getElementById('percentLabel');
    const bar = document.getElementById('bar');
    const downloadLink = document.getElementById('downloadLink');
    const downloadHint = document.getElementById('downloadHint');

    progressCard.classList.remove('hidden');
    downloadLink.classList.add('hidden');
    downloadHint.classList.add('hidden');
    bar.style.width = '0%';
    percentLabel.innerText = '0%';
    statusText.innerText = 'Status: Starting…';

    // Only "video" downloads honor a resolution cap - TikTok's format
    // selection already targets a specific stream, and audio has no
    // resolution concept.
    const resolution = dtype === 'video'
        ? document.getElementById('resolutionSelect').value
        : '';

    fetch("/download", {
        method: "POST",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body: `url=${encodeURIComponent(urlInput.value)}&type=${dtype}&resolution=${encodeURIComponent(resolution)}`
    });

    const evt = new EventSource("/progress");
    evt.onmessage = e => {
        const data = JSON.parse(e.data);
        bar.style.width = data.percent + '%';
        percentLabel.innerText = data.percent + '%';
        statusText.innerText = `Status: ${data.status}`;

        if (data.status === "done") {
            evt.close();
            statusText.innerText = "✅ Download complete";

            // Tried linking to an inline (non-attachment) copy so iOS's
            // native Share sheet could offer "Save Video" directly - but
            // in practice this routed through whatever app iOS/the
            // browser's "Open in..." picker defaulted to (a DJ app, in
            // one real test), not reliably Safari's own player. A plain
            // forced download -> Files app -> Share -> Save Video is
            // less convenient but actually works consistently, so that's
            // what every platform gets now.
            const filePath = "/downloads/" + encodeURIComponent(data.filename);
            downloadHint.classList.add('hidden');

            // Fire the browser download automatically - no click needed.
            // The server deletes the file from downloads/ as soon as this
            // request finishes serving it, so this fetch must be the only
            // thing that ever requests this filePath; a plain `<a>` click
            // would work too but a temporary, off-DOM anchor keeps it from
            // lingering as a (now-dead) button in the UI.
            const tempLink = document.createElement('a');
            tempLink.href = filePath;
            tempLink.download = data.filename;
            document.body.appendChild(tempLink);
            tempLink.click();
            tempLink.remove();

            statusText.innerText = "✅ Downloaded";
        }

        if (data.status.startsWith("error")) {
            evt.close();
            statusText.innerText = "❌ " + data.status;
        }
    };
}
