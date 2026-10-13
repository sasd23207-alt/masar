'use strict';

const { app, BrowserWindow, dialog, session } = require('electron');
const { spawn } = require('child_process');
const fs = require('fs');
const http = require('http');
const path = require('path');

const HOST = '127.0.0.1';
const PORT = 8787;
const BASE_URL = `http://${HOST}:${PORT}`;
const PRODUCT_DATA = path.join(process.env.ProgramData || path.join(process.env.SystemDrive || 'C:', 'ProgramData'), 'Masar');
const DB_DIR = path.join(PRODUCT_DATA, 'data');
const BACKUP_DIR = path.join(PRODUCT_DATA, 'backups');
const LOG_DIR = path.join(PRODUCT_DATA, 'logs');
const CONFIG_FILE = path.join(PRODUCT_DATA, '.env');

let mainWindow = null;
let backend = null;
let isQuitting = false;
let backendLog = null;

const singleInstance = app.requestSingleInstanceLock();
if (!singleInstance) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(startApplication).catch((error) => {
    dialog.showErrorBox(
      'تعذر تشغيل مِسار',
      `لم يتمكن مِسار من بدء التشغيل.\n\n${error.message}\n\nراجع ملف السجل:\n${path.join(LOG_DIR, 'server.log')}`
    );
    app.quit();
  });
}

function ensureFolders() {
  [PRODUCT_DATA, DB_DIR, BACKUP_DIR, LOG_DIR].forEach((folder) => {
    fs.mkdirSync(folder, { recursive: true });
  });
}

function readAccessCode() {
  if (!fs.existsSync(CONFIG_FILE)) return '';
  const lines = fs.readFileSync(CONFIG_FILE, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const separator = trimmed.indexOf('=');
    if (separator < 1) continue;
    const key = trimmed.slice(0, separator).trim();
    const value = trimmed.slice(separator + 1).trim();
    if (key === 'MASAR_ACCESS_CODE') return value.replace(/^["']|["']$/g, '');
  }
  return '';
}

function healthCheck() {
  return new Promise((resolve) => {
    const request = http.get({
      hostname: HOST,
      port: PORT,
      path: '/api/health',
      timeout: 1200
    }, (response) => {
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => { body += chunk; });
      response.on('end', () => {
        try {
          const result = JSON.parse(body);
          resolve(response.statusCode === 200 && result.ok === true && result.database === 'sqlite');
        } catch (_) {
          resolve(false);
        }
      });
    });
    request.on('timeout', () => { request.destroy(); resolve(false); });
    request.on('error', () => resolve(false));
  });
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForBackend(timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (backend && backend.exitCode !== null) {
      throw new Error(`خادم مِسار توقف أثناء التشغيل (exit code ${backend.exitCode}). راجع ملف السجل.`);
    }
    if (await healthCheck()) return;
    await delay(300);
  }
  throw new Error('لم يبدأ خادم مِسار خلال المهلة المحددة. تأكد من عدم وجود برنامج آخر يستخدم المنفذ 8787 ثم راجع ملف السجل.');
}

function startBackend() {
  const backendExe = path.join(process.resourcesPath, 'backend', 'MasarServer', 'MasarServer.exe');
  if (!fs.existsSync(backendExe)) {
    throw new Error(`ملفات التشغيل غير مكتملة: ${backendExe}`);
  }

  const accessCode = readAccessCode();
  const childEnv = { ...process.env };
  childEnv.MASAR_DESKTOP_MODE = '1';
  childEnv.MASAR_HOST = HOST;
  childEnv.MASAR_PORT = String(PORT);
  childEnv.MASAR_DB = path.join(DB_DIR, 'masar.db');
  childEnv.MASAR_BACKUPS = BACKUP_DIR;
  delete childEnv.WHATSAPP_ACCESS_TOKEN;
  delete childEnv.WHATSAPP_PHONE_NUMBER_ID;
  delete childEnv.WHATSAPP_TEMPLATE_NAME;
  delete childEnv.WHATSAPP_VERIFY_TOKEN;
  delete childEnv.WHATSAPP_APP_SECRET;
  delete childEnv.WHATSAPP_GRAPH_URL;
  childEnv.WHATSAPP_MODE = 'template';
  if (accessCode) childEnv.MASAR_ACCESS_CODE = accessCode;
  else delete childEnv.MASAR_ACCESS_CODE;

  backendLog = fs.createWriteStream(path.join(LOG_DIR, 'server.log'), { flags: 'a' });
  backendLog.write(`\r\n--- مِسار بدأ ${new Date().toISOString()} ---\r\n`);
  backend = spawn(backendExe, [], {
    cwd: path.dirname(backendExe),
    env: childEnv,
    windowsHide: true,
    stdio: ['ignore', 'pipe', 'pipe']
  });
  backend.stdout.on('data', (chunk) => backendLog && backendLog.write(chunk));
  backend.stderr.on('data', (chunk) => backendLog && backendLog.write(chunk));
  backend.on('error', (error) => backendLog && backendLog.write(`\r\nBackend spawn error: ${error.message}\r\n`));
  backend.on('exit', (code, signal) => {
    if (!isQuitting && backendLog) backendLog.write(`\r\nBackend exited: code=${code}, signal=${signal}\r\n`);
  });
}

async function startApplication() {
  // Keep this desktop build fully local: only the bundled loopback API is allowed.
  // External requests (including optional web fonts) are blocked so core use needs no internet.
  session.defaultSession.webRequest.onBeforeRequest(
    { urls: ['http://*/*', 'https://*/*'] },
    (details, callback) => {
      try {
        const url = new URL(details.url);
        const isLocalMasar = url.protocol === 'http:' &&
          url.hostname === HOST && url.port === String(PORT);
        callback({ cancel: !isLocalMasar });
      } catch (_) {
        callback({ cancel: true });
      }
    }
  );

  ensureFolders();
  startBackend();
  await waitForBackend(30000);

  mainWindow = new BrowserWindow({
    width: 1360,
    height: 900,
    minWidth: 840,
    minHeight: 600,
    show: false,
    autoHideMenuBar: true,
    backgroundColor: '#FBF7F1',
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true
    }
  });
  mainWindow.setMenuBarVisibility(false);
  mainWindow.once('ready-to-show', () => mainWindow && mainWindow.show());
  mainWindow.on('closed', () => { mainWindow = null; });
  await mainWindow.loadURL(BASE_URL);
}

app.on('before-quit', () => {
  isQuitting = true;
  if (backend && backend.exitCode === null) {
    try { backend.kill(); } catch (_) { /* already stopped */ }
  }
  if (backendLog) {
    try { backendLog.end(); } catch (_) { /* already closed */ }
    backendLog = null;
  }
});

app.on('window-all-closed', () => {
  app.quit();
});
