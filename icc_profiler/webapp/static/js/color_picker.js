document.addEventListener('DOMContentLoaded', () => {
    const picker = document.querySelector('.color-picker');
    if (!picker || typeof pdfjsLib === 'undefined') return;

    const fileInput = document.getElementById('color-picker-file');
    const preview = document.getElementById('color-picker-preview');
    const canvas = document.getElementById('color-picker-canvas');
    const swatch = document.getElementById('color-picker-swatch');
    const valueLabel = document.getElementById('color-picker-value');
    const pageControls = document.getElementById('color-picker-page-controls');
    const pageInput = document.getElementById('color-picker-page');
    const pageTotal = document.getElementById('color-picker-page-total');
    const pageGoBtn = document.getElementById('color-picker-page-go');

    const colorspace = picker.dataset.colorspace;
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    const MAX_WIDTH = 480;

    pdfjsLib.GlobalWorkerOptions.workerSrc =
        'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.5.141/pdf.worker.min.js';

    let pdfDoc = null;

    fileInput.addEventListener('change', async () => {
        const file = fileInput.files[0];
        if (!file) return;
        valueLabel.textContent = 'Loading PDF...';
        try {
            const data = await file.arrayBuffer();
            pdfDoc = await pdfjsLib.getDocument({ data }).promise;
        } catch (err) {
            valueLabel.textContent = 'Could not read PDF: ' + err.message;
            return;
        }
        pageTotal.textContent = pdfDoc.numPages;
        pageInput.value = 1;
        pageInput.max = pdfDoc.numPages;
        pageControls.hidden = pdfDoc.numPages <= 1;
        await renderPage(1);
    });

    pageGoBtn.addEventListener('click', () => {
        if (!pdfDoc) return;
        const n = Math.min(Math.max(1, parseInt(pageInput.value, 10) || 1), pdfDoc.numPages);
        pageInput.value = n;
        renderPage(n);
    });

    async function renderPage(n) {
        const page = await pdfDoc.getPage(n);
        const baseViewport = page.getViewport({ scale: 1 });
        const scale = Math.min(1, MAX_WIDTH / baseViewport.width);
        const viewport = page.getViewport({ scale });
        canvas.width = Math.round(viewport.width);
        canvas.height = Math.round(viewport.height);
        await page.render({ canvasContext: ctx, viewport }).promise;
        valueLabel.textContent = 'Click the page to sample a color.';
        preview.hidden = false;
    }

    canvas.addEventListener('click', (e) => {
        const rect = canvas.getBoundingClientRect();
        const x = Math.round((e.clientX - rect.left) * (canvas.width / rect.width));
        const y = Math.round((e.clientY - rect.top) * (canvas.height / rect.height));
        const [r, g, b] = ctx.getImageData(x, y, 1, 1).data;
        swatch.style.background = `rgb(${r},${g},${b})`;
        applyColor(r, g, b);
    });

    function applyColor(r, g, b) {
        if (colorspace === 'CMYK') {
            const { c, m, y, k } = rgbToCmyk(r, g, b);
            setField('color-field-cmyk-c', c);
            setField('color-field-cmyk-m', m);
            setField('color-field-cmyk-y', y);
            setField('color-field-cmyk-k', k);
            valueLabel.textContent =
                `C ${c}% M ${m}% Y ${y}% K ${k}% (approximate, converted from the rendered page's on-screen color)`;
        } else if (colorspace === 'GRAY') {
            const k = rgbToGrayPercent(r, g, b);
            setField('color-field-gray', k);
            valueLabel.textContent =
                `K ${k}% (approximate, converted from the rendered page's on-screen color)`;
        } else {
            const hex = rgbToHex(r, g, b);
            setField('color-field-hex', hex);
            valueLabel.textContent = hex;
        }
    }

    function setField(id, value) {
        const el = document.getElementById(id);
        if (el) el.value = value;
    }

    function rgbToHex(r, g, b) {
        return '#' + [r, g, b].map((v) => v.toString(16).padStart(2, '0')).join('').toUpperCase();
    }

    function rgbToGrayPercent(r, g, b) {
        const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
        return Math.round((1 - lum / 255) * 1000) / 10;
    }

    function rgbToCmyk(r, g, b) {
        const rn = r / 255, gn = g / 255, bn = b / 255;
        const k = 1 - Math.max(rn, gn, bn);
        const round = (v) => Math.round(v * 1000) / 10;
        if (k >= 1) return { c: 0, m: 0, y: 0, k: 100 };
        return {
            c: round((1 - rn - k) / (1 - k)),
            m: round((1 - gn - k) / (1 - k)),
            y: round((1 - bn - k) / (1 - k)),
            k: round(k),
        };
    }
});
