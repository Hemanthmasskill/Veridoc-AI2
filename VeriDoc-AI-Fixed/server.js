/**
 * VeriDoc - Automated Multi-Document Verification and Tamper Detection System
 * Backend Server (Node.js & Express)
 * Developed for MCA Mini Project | CAF 7104
 */
const express = require('express');
const multer = require('multer');
const cors = require('cors');
const path = require('path');
const crypto = require('crypto');
const { spawnSync, spawn } = require('child_process');
const { v4: uuidv4 } = require('uuid');
const fs = require('fs');

let PythonShell = null;
try {
  PythonShell = require('python-shell').PythonShell;
} catch {
  PythonShell = null;
}

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json({ limit: '1mb' }));

// Security headers (defense-in-depth against XSS, clickjacking, and MIME sniffing)
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'SAMEORIGIN');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  next();
});

app.use(express.static(path.join(__dirname, 'public')));

// ---------------------------------------------------------------------------
// Cross-platform Python interpreter resolution (Windows 11 + Linux/macOS)
// ---------------------------------------------------------------------------
function resolvePythonExecutable() {
  if (process.env.VERIDOC_PYTHON && fs.existsSync(process.env.VERIDOC_PYTHON)) {
    return process.env.VERIDOC_PYTHON;
  }
  const candidates = [
    path.join(__dirname, 'ml', 'venv', 'Scripts', 'python.exe'),
    path.join(__dirname, 'venv', 'Scripts', 'python.exe'),
    path.join(__dirname, 'ml', 'venv', 'bin', 'python'),
    path.join(__dirname, 'ml', 'venv', 'bin', 'python3'),
    path.join(__dirname, 'venv', 'bin', 'python'),
    path.join(__dirname, 'venv', 'bin', 'python3'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(c)) return c;
  }
  const binNames = process.platform === 'win32' ? ['python', 'py', 'python3'] : ['python3', 'python'];
  for (const bin of binNames) {
    try {
      const check = spawnSync(bin, ['--version'], { timeout: 3000, windowsHide: true });
      if (check.status === 0) return bin;
    } catch {
      // try next
    }
  }
  return process.platform === 'win32' ? 'python' : 'python3';
}

const PYTHON_BIN = resolvePythonExecutable();

function runPythonScript(scriptName, args = [], timeoutMs = 90000) {
  const scriptDir = path.join(__dirname, 'ml');
  const scriptFullPath = path.join(scriptDir, scriptName);
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON_BIN, ['-u', scriptFullPath, ...args], {
      cwd: scriptDir,
      windowsHide: true,
      env: { ...process.env, PYTHONIOENCODING: 'utf-8' }
    });
    let stdout = '';
    let stderr = '';
    let killedByTimeout = false;

    const timer = setTimeout(() => {
      killedByTimeout = true;
      try { child.kill('SIGKILL'); } catch {}
      reject(new Error(`Python script '${scriptName}' timed out after ${Math.round(timeoutMs / 1000)}s`));
    }, timeoutMs);

    child.stdout.on('data', (chunk) => { stdout += chunk.toString('utf8'); });
    child.stderr.on('data', (chunk) => { stderr += chunk.toString('utf8'); });

    child.on('error', (err) => {
      clearTimeout(timer);
      reject(err);
    });

    child.on('close', (code) => {
      clearTimeout(timer);
      if (killedByTimeout) return;
      const lines = stdout.split(/\r?\n/).map(l => l.trim()).filter(Boolean);
      if (code !== 0 && !lines.length) {
        return reject(new Error(stderr.trim() || `Python exited with code ${code}`));
      }
      resolve(lines);
    });
  });
}

// ---------------------------------------------------------------------------
// File Upload Configuration (Multer)
// ---------------------------------------------------------------------------
const storage = multer.diskStorage({
  destination: (req, file, cb) => {
    const uploadDir = path.join(__dirname, 'uploads');
    if (!fs.existsSync(uploadDir)) {
      fs.mkdirSync(uploadDir, { recursive: true });
    }
    cb(null, uploadDir);
  },
  filename: (req, file, cb) => {
    const safeExt = path.extname(path.basename(file.originalname || '')).toLowerCase();
    const uniqueName = `${uuidv4()}${safeExt}`;
    cb(null, uniqueName);
  }
});

const upload = multer({
  storage,
  limits: { fileSize: 15 * 1024 * 1024 }, // 15 MB
  fileFilter: (req, file, cb) => {
    const allowedExt = /^\.(jpeg|jpg|png|webp|pdf)$/i;
    const ext = allowedExt.test(path.extname(file.originalname || '').toLowerCase());
    const mimeOk = /image\/(jpeg|jpg|png|webp)|application\/pdf|application\/octet-stream/i.test(file.mimetype || '');
    if (ext && mimeOk) cb(null, true);
    else cb(new Error('Only images (JPG, JPEG, PNG, WEBP) and PDFs up to 15 MB are allowed'));
  }
});

const ALLOWED_TYPES = ['auto', 'aadhaar', 'pan', 'passport', 'driving_licence', 'marksheet', 'certificate', 'generic'];

// ---------------------------------------------------------------------------
// VeriDoc Organization Auth (local scrypt hashes + opaque session tokens,
// atomic JSON file store, HttpOnly cookies, brute-force rate limiting)
// ---------------------------------------------------------------------------
const DATA_DIR = path.join(__dirname, 'data');
const USERS_FILE = path.join(DATA_DIR, 'users.json');
const SESSIONS_FILE = path.join(DATA_DIR, 'sessions.json');
const SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000;

const authRateMap = new Map();
function checkAuthRateLimit(ip) {
  const now = Date.now();
  const windowMs = 60 * 1000;
  const maxAttempts = 15;
  const rec = authRateMap.get(ip) || { count: 0, resetAt: now + windowMs };
  if (now > rec.resetAt) {
    rec.count = 0;
    rec.resetAt = now + windowMs;
  }
  rec.count += 1;
  authRateMap.set(ip, rec);
  return rec.count <= maxAttempts;
}

function readJson(file, fallback) {
  try {
    if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
    if (!fs.existsSync(file)) return fallback;
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch { return fallback; }
}

function writeJson(file, obj) {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  const tmpFile = `${file}.${process.pid}.${Date.now()}.tmp`;
  fs.writeFileSync(tmpFile, JSON.stringify(obj, null, 2));
  fs.renameSync(tmpFile, file);
}

function pruneExpiredSessions(sessions) {
  const now = Date.now();
  let changed = false;
  for (const [tok, s] of Object.entries(sessions)) {
    if (!s || now - (s.created || 0) > SESSION_TTL_MS) {
      delete sessions[tok];
      changed = true;
    }
  }
  return changed;
}

function hashPassword(password) {
  const salt = crypto.randomBytes(16).toString('hex');
  const hash = crypto.scryptSync(password, salt, 64).toString('hex');
  return `scrypt$${salt}$${hash}`;
}

function checkPassword(password, stored) {
  try {
    const [, salt, hash] = String(stored).split('$');
    if (!salt || !hash) return false;
    const test = crypto.scryptSync(password, salt, 64).toString('hex');
    return crypto.timingSafeEqual(Buffer.from(test, 'hex'), Buffer.from(hash, 'hex'));
  } catch { return false; }
}

function parseCookies(req) {
  const out = {};
  (req.headers.cookie || '').split(';').forEach(p => {
    const i = p.indexOf('=');
    if (i > 0) {
      try {
        out[p.slice(0, i).trim()] = decodeURIComponent(p.slice(i + 1).trim());
      } catch {
        out[p.slice(0, i).trim()] = p.slice(i + 1).trim();
      }
    }
  });
  return out;
}

function setSession(res, email) {
  const sessions = readJson(SESSIONS_FILE, {});
  pruneExpiredSessions(sessions);
  const token = crypto.randomBytes(32).toString('hex');
  sessions[token] = { email, created: Date.now() };
  writeJson(SESSIONS_FILE, sessions);
  res.setHeader('Set-Cookie', [
    `veridoc_session=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${SESSION_TTL_MS / 1000}`,
    `dv_session=${token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=${SESSION_TTL_MS / 1000}`
  ]);
}

function clearSession(req, res) {
  const cookies = parseCookies(req);
  const token = cookies.veridoc_session || cookies.dv_session;
  if (token) {
    const sessions = readJson(SESSIONS_FILE, {});
    delete sessions[token];
    pruneExpiredSessions(sessions);
    writeJson(SESSIONS_FILE, sessions);
  }
  res.setHeader('Set-Cookie', [
    'veridoc_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0',
    'dv_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0'
  ]);
}

function getSessionOrg(req) {
  const cookies = parseCookies(req);
  const token = cookies.veridoc_session || cookies.dv_session;
  if (!token) return null;
  const sessions = readJson(SESSIONS_FILE, {});
  const s = sessions[token];
  if (!s || Date.now() - s.created > SESSION_TTL_MS) {
    if (s) {
      delete sessions[token];
      writeJson(SESSIONS_FILE, sessions);
    }
    return null;
  }
  const users = readJson(USERS_FILE, {});
  const u = users[String(s.email).toLowerCase()];
  return u ? { orgName: u.orgName, email: u.email, created: u.created } : null;
}

function requireAuth(req, res, next) {
  const org = getSessionOrg(req);
  if (!org) return res.status(401).json({ error: 'Sign in required', auth: false });
  req.org = org;
  next();
}

function optionalAuth(req, res, next) {
  req.org = getSessionOrg(req);
  next();
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

// POST /api/auth/signup { orgName, email, password }
app.post('/api/auth/signup', (req, res) => {
  if (!checkAuthRateLimit(req.ip || 'local')) {
    return res.status(429).json({ error: 'Too many authentication attempts. Please wait a minute.' });
  }
  const orgName = String((req.body && req.body.orgName) || '').trim();
  const email = String((req.body && req.body.email) || '').trim().toLowerCase();
  const password = String((req.body && req.body.password) || '');
  if (orgName.length < 2 || orgName.length > 120) {
    return res.status(400).json({ error: 'Organization name must be between 2 and 120 characters' });
  }
  if (!EMAIL_RE.test(email)) return res.status(400).json({ error: 'Valid email required' });
  if (password.length < 8) return res.status(400).json({ error: 'Password must be at least 8 characters' });
  const users = readJson(USERS_FILE, {});
  if (users[email]) return res.status(409).json({ error: 'An account with this email already exists' });
  users[email] = { orgName, email, pass: hashPassword(password), created: new Date().toISOString() };
  writeJson(USERS_FILE, users);
  setSession(res, email);
  res.json({ ok: true, org: { orgName, email } });
});

// POST /api/auth/login { email, password }
app.post('/api/auth/login', (req, res) => {
  if (!checkAuthRateLimit(req.ip || 'local')) {
    return res.status(429).json({ error: 'Too many login attempts. Please wait a minute.' });
  }
  const email = String((req.body && req.body.email) || '').trim().toLowerCase();
  const password = String((req.body && req.body.password) || '');
  const users = readJson(USERS_FILE, {});
  const u = users[email];
  if (!u || !checkPassword(password, u.pass)) {
    return res.status(401).json({ error: 'Incorrect email or password' });
  }
  setSession(res, email);
  res.json({ ok: true, org: { orgName: u.orgName, email: u.email } });
});

// POST /api/auth/logout
app.post('/api/auth/logout', (req, res) => {
  clearSession(req, res);
  res.json({ ok: true });
});

// GET /api/auth/me -> current org or 401
app.get('/api/auth/me', (req, res) => {
  const org = getSessionOrg(req);
  if (!org) return res.status(401).json({ auth: false });
  res.json({ auth: true, org });
});

// POST /api/upload — accepts `document` file + `documentType`, returns verification JSON
app.post('/api/upload', optionalAuth, upload.single('document'), async (req, res) => {
  let filePath = null;
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });
    filePath = req.file.path;
    let documentType = String((req.body && req.body.documentType) || 'auto').toLowerCase();
    if (!ALLOWED_TYPES.includes(documentType)) documentType = 'auto';

    console.log(`[upload] ${req.org ? req.org.orgName : 'guest'} -> ${req.file.originalname} -> ${documentType}`);
    const result = await processDocument(filePath, documentType);
    if (result && result.error) {
      return res.status(422).json(result);
    }
    res.json(result);
  } catch (error) {
    console.error('Upload error:', error.message);
    res.status(500).json({ error: error.message || 'VeriDoc verification failed' });
  } finally {
    if (filePath && fs.existsSync(filePath)) {
      fs.unlink(filePath, (err) => {
        if (err) console.error('Failed to delete temp file:', err.message);
      });
    }
  }
});

// JS -> Python bridge with cross-platform interpreter & 90s timeout
async function processDocument(filePath, documentType) {
  try {
    const results = await runPythonScript('verify_document.py', [filePath, documentType], 90000);
    if (!results || !results.length) throw new Error('Empty response from VeriDoc ML service');
    const lastLine = results[results.length - 1];
    return JSON.parse(lastLine);
  } catch (err) {
    console.error('VeriDoc Python ML error:', err.message);
    throw new Error(
      'VeriDoc ML service failed. Ensure Python dependencies are installed (`pip install -r ml/requirements.txt`). Details: ' + err.message
    );
  }
}

app.get('/api/health', async (req, res) => {
  let engines = null;
  try {
    const out = await runPythonScript('check_engines.py', [], 15000);
    engines = JSON.parse(out[out.length - 1]);
  } catch (e) {
    engines = { error: 'engine check failed: ' + e.message };
  }
  res.json({ status: 'ok', service: 'veridoc-system', pythonPath: PYTHON_BIN, engines });
});

app.get('/api/doc-types', (req, res) => {
  res.json(ALLOWED_TYPES.map(t => ({ value: t })));
});

// Multer / validation errors -> clean JSON
app.use((err, req, res, next) => {
  if (err) {
    const status = err.code === 'LIMIT_FILE_SIZE' ? 413 : 400;
    return res.status(status).json({ error: err.message });
  }
  next();
});

if (require.main === module) {
  app.listen(PORT, () => {
    console.log(`VeriDoc running on http://localhost:${PORT} (Python: ${PYTHON_BIN})`);
    console.log(`Make sure Python deps are installed: pip install -r ml/requirements.txt`);
  });
}

module.exports = app;
