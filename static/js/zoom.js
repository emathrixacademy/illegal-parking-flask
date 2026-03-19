/**
 * Digital Zoom Module (Feature 15)
 * Adds interactive zoom and pan to live MJPEG feeds and playback.
 * Supports mouse wheel, click-drag, touch pinch-to-zoom, and double-click reset.
 */
class DigitalZoom {
    constructor(imgElement, container) {
        this.img = imgElement;
        this.container = container;
        this.scale = 1;
        this.minScale = 1;
        this.maxScale = 8;
        this.translateX = 0;
        this.translateY = 0;
        this.isDragging = false;
        this.startX = 0;
        this.startY = 0;

        this.init();
    }

    init() {
        // Prevent default context menu on the container
        this.container.style.overflow = 'hidden';
        this.container.style.position = 'relative';
        this.img.style.transformOrigin = '0 0';
        this.img.style.transition = 'none';

        // Mouse wheel zoom
        this.container.addEventListener('wheel', (e) => {
            e.preventDefault();
            const delta = e.deltaY > 0 ? -0.3 : 0.3;
            const rect = this.container.getBoundingClientRect();
            this.zoom(delta, e.clientX - rect.left, e.clientY - rect.top);
        });

        // Click-drag pan
        this.container.addEventListener('mousedown', (e) => {
            if (this.scale > 1) {
                this.isDragging = true;
                this.startX = e.clientX - this.translateX;
                this.startY = e.clientY - this.translateY;
                this.container.style.cursor = 'grabbing';
                e.preventDefault();
            }
        });

        document.addEventListener('mousemove', (e) => {
            if (this.isDragging) {
                this.translateX = e.clientX - this.startX;
                this.translateY = e.clientY - this.startY;
                this.clampTranslation();
                this.applyTransform();
            }
        });

        document.addEventListener('mouseup', () => {
            this.isDragging = false;
            this.container.style.cursor = this.scale > 1 ? 'grab' : 'default';
        });

        // Touch pinch-to-zoom (mobile)
        let initialDistance = 0;
        let initialScale = 1;
        let touchStartX = 0, touchStartY = 0;
        let isTouchDragging = false;

        this.container.addEventListener('touchstart', (e) => {
            if (e.touches.length === 2) {
                initialDistance = this.getTouchDistance(e.touches);
                initialScale = this.scale;
            } else if (e.touches.length === 1 && this.scale > 1) {
                isTouchDragging = true;
                touchStartX = e.touches[0].clientX - this.translateX;
                touchStartY = e.touches[0].clientY - this.translateY;
            }
        });

        this.container.addEventListener('touchmove', (e) => {
            if (e.touches.length === 2) {
                e.preventDefault();
                const dist = this.getTouchDistance(e.touches);
                const newScale = initialScale * (dist / initialDistance);
                this.scale = Math.min(this.maxScale, Math.max(this.minScale, newScale));
                this.clampTranslation();
                this.applyTransform();
            } else if (e.touches.length === 1 && isTouchDragging) {
                e.preventDefault();
                this.translateX = e.touches[0].clientX - touchStartX;
                this.translateY = e.touches[0].clientY - touchStartY;
                this.clampTranslation();
                this.applyTransform();
            }
        });

        this.container.addEventListener('touchend', () => {
            isTouchDragging = false;
        });

        // Double-click to reset
        this.container.addEventListener('dblclick', () => this.reset());
    }

    zoom(delta, mouseX, mouseY) {
        const prevScale = this.scale;
        this.scale = Math.min(this.maxScale, Math.max(this.minScale, this.scale + delta));

        // Zoom towards mouse position
        const ratio = this.scale / prevScale;
        this.translateX = mouseX - ratio * (mouseX - this.translateX);
        this.translateY = mouseY - ratio * (mouseY - this.translateY);
        this.clampTranslation();
        this.applyTransform();
        this.updateZoomLabel();
    }

    reset() {
        this.scale = 1;
        this.translateX = 0;
        this.translateY = 0;
        this.applyTransform();
        this.updateZoomLabel();
        this.container.style.cursor = 'default';
    }

    applyTransform() {
        this.img.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.scale})`;
    }

    clampTranslation() {
        const rect = this.container.getBoundingClientRect();
        const imgW = rect.width * this.scale;
        const imgH = rect.height * this.scale;
        const maxX = 0;
        const minX = rect.width - imgW;
        const maxY = 0;
        const minY = rect.height - imgH;
        this.translateX = Math.min(maxX, Math.max(minX, this.translateX));
        this.translateY = Math.min(maxY, Math.max(minY, this.translateY));
    }

    updateZoomLabel() {
        // Update zoom level label if exists
        const label = this.container.querySelector('.zoom-level');
        if (label) {
            label.textContent = this.scale.toFixed(1) + 'x';
        }
    }

    getTouchDistance(touches) {
        const dx = touches[0].clientX - touches[1].clientX;
        const dy = touches[0].clientY - touches[1].clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }
}
