// SPDX-License-Identifier: AGPL-3.0-or-later
const express = require('express');
const path = require('path');
const fs = require('fs');
const helmet = require('helmet');
const morgan = require('morgan');
const compression = require('compression');
const { createProxyMiddleware } = require('http-proxy-middleware');
const escapeHtml = require('escape-html');

const app = express();
const PORT = process.env.PORT || 3000;
const NODE_ENV = process.env.NODE_ENV || 'development';

const distDir = path.join(__dirname, 'dist');
const publicDir = path.join(__dirname, 'public');
const hasDistDir = fs.existsSync(distDir);

// ---------------------------------------------------------------------------
// Middleware
// ---------------------------------------------------------------------------

// Security middleware — CSP disabled because the D3 chart library uses inline
// styles and dynamically-created script patterns. Re-evaluate if charts are
// ported to a bundled module.
app.use(helmet({
    contentSecurityPolicy: false,
    crossOriginEmbedderPolicy: false
}));

// Compression middleware - gzip responses
app.use(compression());

// HTTP request logging (development only)
if (NODE_ENV === 'development') {
    app.use(morgan('dev'));
}

// ---------------------------------------------------------------------------
// API proxy (development — in production, nginx handles /api/ routing)
// ---------------------------------------------------------------------------
const API_TARGET = process.env.API_URL || 'http://localhost:5001';
app.use(createProxyMiddleware({
    target: API_TARGET,
    changeOrigin: true,
    pathFilter: '/api/**',
}));

// ---------------------------------------------------------------------------
// Static assets
// ---------------------------------------------------------------------------

// Serve Vite build output (hashed JS/CSS bundles, chart library, logos, etc.)
// index: false so the SPA catch-all below handles HTML routing.
if (hasDistDir) {
    app.use(express.static(distDir, { index: false }));
} else {
    // No build yet — serve directly from public/ during development
    app.use(express.static(publicDir, { index: false }));
}

// Serve documentation
app.use('/docs', express.static(path.join(__dirname, 'docs')));

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

// Health check endpoint
app.get('/health', (req, res) => {
    res.status(200).json({
        status: 'ok',
        timestamp: new Date().toISOString(),
        uptime: process.uptime(),
        environment: NODE_ENV
    });
});

// ---------------------------------------------------------------------------
// React SPA catch-all
// ---------------------------------------------------------------------------
// All non-file requests are served the React app's index.html.
// React Router handles client-side routing from there.
const BASE_PATH = process.env.BASE_PATH || '';

const indexHtmlPath = path.join(distDir, 'index.html');
const hasIndexHtml = hasDistDir && fs.existsSync(indexHtmlPath);
let indexHtml = null;
if (hasIndexHtml) {
    const raw = fs.readFileSync(indexHtmlPath, 'utf-8');
    indexHtml = BASE_PATH
        ? raw.replace('<head>', `<head>\n    <meta name="base-path" content="${escapeHtml(BASE_PATH)}" />`)
        : raw;
}

if (hasIndexHtml) {
    app.get('*', (req, res, next) => {
        // Skip requests that look like static file paths (have a file extension)
        if (/\.[^/]+$/.test(req.path)) return next();
        res.set('Content-Type', 'text/html');
        res.send(indexHtml);
    });
}

// ---------------------------------------------------------------------------
// Error handling
// ---------------------------------------------------------------------------

// 404 handler - must be after all routes
app.use((req, res) => {
    res.status(404).json({
        error: 'Not Found',
        path: req.path,
        message: 'The requested resource was not found on this server'
    });
});

// Global error handler - must be last
app.use((err, req, res, next) => {
    console.error('[Error]', err.stack);
    res.status(err.status || 500).json({
        error: 'Internal Server Error',
        message: NODE_ENV === 'development' ? err.message : 'An unexpected error occurred'
    });
});

// Handle uncaught exceptions
process.on('uncaughtException', (err) => {
    console.error('[Fatal] Uncaught Exception:', err);
    process.exit(1);
});

// Handle unhandled promise rejections
process.on('unhandledRejection', (reason, promise) => {
    console.error('[Fatal] Unhandled Rejection at:', promise, 'reason:', reason);
    process.exit(1);
});

// ---------------------------------------------------------------------------
// Start server
// ---------------------------------------------------------------------------
app.listen(PORT, () => {
    const timestamp = new Date().toISOString();
    console.log(`[${timestamp}] AssetAtlas UI Server started`);
    console.log(`[${timestamp}] Environment: ${NODE_ENV}`);
    console.log(`[${timestamp}] Listening on http://localhost:${PORT}`);
    if (hasIndexHtml) {
        console.log(`[${timestamp}] Serving React SPA from dist/`);
    }
});
