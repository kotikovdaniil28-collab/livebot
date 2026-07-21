// Запускатель для хостингов с ядром «node.js + python3» (Pterodactyl/Play2GO).
// Панель всегда стартует через Node и ищет index.js — этот файл:
//   1) находит Python-пакеты, установленные хостингом в .local,
//   2) если в контейнере нет pip — сам скачивает get-pip.py и устанавливает pip,
//   3) ставит зависимости из requirements.txt в .local,
//   4) запускает Python-бота с правильным PYTHONPATH и передаёт его логи в консоль.
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const https = require("https");

const CWD = __dirname;
const REQUIREMENTS = path.join(CWD, "requirements.txt");
const LOCAL_PREFIX = path.join(CWD, ".local");
const GET_PIP = path.join(CWD, ".get-pip.py");

// Находим рабочую команду Python.
function findPython() {
	for (const cmd of ["python3", "python"]) {
		const res = spawnSync(cmd, ["--version"], { stdio: "ignore" });
		if (!res.error && res.status === 0) return cmd;
	}
	return null;
}

// Собираем все каталоги site-packages внутри .local и ~/.local.
function localSitePackages() {
	const dirs = [];
	const roots = [path.join(LOCAL_PREFIX, "lib")];
	if (process.env.HOME) roots.push(path.join(process.env.HOME, ".local", "lib"));
	for (const libRoot of roots) {
		if (!fs.existsSync(libRoot)) continue;
		for (const entry of fs.readdirSync(libRoot)) {
			const sp = path.join(libRoot, entry, "site-packages");
			if (fs.existsSync(sp) && !dirs.includes(sp)) dirs.push(sp);
		}
	}
	return dirs;
}

// Окружение для Python с учётом .local.
function pyEnv() {
	const extra = localSitePackages();
	const env = { ...process.env };
	const parts = [...extra];
	if (env.PYTHONPATH) parts.push(env.PYTHONPATH);
	if (parts.length) env.PYTHONPATH = parts.join(path.delimiter);
	return env;
}

// Проверяем, что у python есть модуль pip.
function hasPipModule(py) {
	const res = spawnSync(py, ["-m", "pip", "--version"], { stdio: "ignore", env: pyEnv() });
	return !res.error && res.status === 0;
}

// Скачиваем файл по https с поддержкой редиректов.
function download(url, dest, redirects = 5) {
	return new Promise((resolve, reject) => {
		if (redirects <= 0) return reject(new Error("Слишком много редиректов"));
		https
			.get(url, (res) => {
				if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
					res.resume();
					return resolve(download(res.headers.location, dest, redirects - 1));
				}
				if (res.statusCode !== 200) {
					res.resume();
					return reject(new Error(`HTTP ${res.statusCode} при скачивании ${url}`));
				}
				const file = fs.createWriteStream(dest);
				res.pipe(file);
				file.on("finish", () => file.close(() => resolve()));
				file.on("error", reject);
			})
			.on("error", reject);
	});
}

// Если pip отсутствует — пробуем ensurepip, затем get-pip.py.
async function bootstrapPip(py) {
	if (hasPipModule(py)) return true;

	console.log("[launcher] pip не найден — пробую ensurepip...");
	const ensure = spawnSync(py, ["-m", "ensurepip", "--user"], { cwd: CWD, stdio: "inherit", env: pyEnv() });
	if (!ensure.error && ensure.status === 0 && hasPipModule(py)) return true;

	console.log("[launcher] ensurepip недоступен — скачиваю get-pip.py...");
	try {
		await download("https://bootstrap.pypa.io/get-pip.py", GET_PIP);
	} catch (err) {
		console.error("[launcher] Не удалось скачать get-pip.py:", err.message);
		return false;
	}

	// Ставим pip в .local, чтобы не требовались root-права.
	const variants = [
		[py, [GET_PIP, "--prefix", LOCAL_PREFIX, "--no-warn-script-location"]],
		[py, [GET_PIP, "--user", "--no-warn-script-location"]],
		[py, [GET_PIP, "--user", "--break-system-packages", "--no-warn-script-location"]],
	];
	for (const [cmd, args] of variants) {
		console.log(`[launcher] Устанавливаю pip: ${cmd} ${args.join(" ")}`);
		const res = spawnSync(cmd, args, { cwd: CWD, stdio: "inherit", env: pyEnv() });
		if (!res.error && res.status === 0 && hasPipModule(py)) {
			console.log("[launcher] pip установлен.");
			try { fs.unlinkSync(GET_PIP); } catch {}
			return true;
		}
	}
	console.error("[launcher] Не удалось установить pip.");
	return false;
}

// Пробуем установить зависимости несколькими способами.
function installDeps(py) {
	const variants = [
		[py, ["-m", "pip", "install", "-U", "--no-cache-dir", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
		[py, ["-m", "pip", "install", "-U", "--no-cache-dir", "--user", "-r", REQUIREMENTS]],
		[py, ["-m", "pip", "install", "-U", "--no-cache-dir", "--user", "--break-system-packages", "-r", REQUIREMENTS]],
		["pip", ["install", "-U", "--no-cache-dir", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
		["pip3", ["install", "-U", "--no-cache-dir", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
	];

	for (const [cmd, args] of variants) {
		console.log(`[launcher] Устанавливаю зависимости: ${cmd} ${args.join(" ")}`);
		const res = spawnSync(cmd, args, { cwd: CWD, stdio: "inherit", env: pyEnv() });
		if (!res.error && res.status === 0) {
			console.log("[launcher] Зависимости установлены.");
			return true;
		}
	}
	console.error("[launcher] Не удалось установить зависимости через pip.");
	return false;
}

// Проверяем, что главный модуль (aiogram) доступен.
function depsReady(py) {
	const res = spawnSync(py, ["-c", "import aiogram"], { cwd: CWD, stdio: "ignore", env: pyEnv() });
	return !res.error && res.status === 0;
}

function startBot(py) {
	console.log(`[launcher] Запускаю Python-бота: ${py} main.py`);
	const bot = spawn(py, ["main.py"], { cwd: CWD, stdio: "inherit", env: pyEnv() });

	bot.on("exit", (code) => {
		console.log(`[launcher] Бот завершился с кодом ${code}.`);
		if (code !== 0) {
			console.log("[launcher] Перезапуск через 10 секунд...");
			setTimeout(() => main(), 10_000);
		} else {
			process.exit(0);
		}
	});

	bot.on("error", (err) => {
		console.error(`[launcher] Не удалось запустить ${py}:`, err.message);
		process.exit(1);
	});
}

async function main() {
	const py = findPython();
	if (!py) {
		console.error("[launcher] Python не найден (пробовал python3 и python).");
		console.log("[launcher] Повторная проверка через 30 секунд...");
		setTimeout(main, 30_000);
		return;
	}

	if (!depsReady(py)) {
		console.log("[launcher] Модуль aiogram не найден — готовлю pip и ставлю зависимости...");
		const pipOk = await bootstrapPip(py);
		if (pipOk) installDeps(py);
	}

	if (!depsReady(py)) {
		console.error("[launcher] aiogram по-прежнему недоступен. Проверьте логи установки выше.");
		console.log("[launcher] Повторная попытка через 60 секунд...");
		setTimeout(main, 60_000);
		return;
	}

	startBot(py);
}

main();
