// Запускатель для хостингов с ядром «node.js + python3» (Pterodactyl/Play2GO).
// Панель всегда стартует через Node и ищет index.js — этот файл:
//   1) находит Python-пакеты, установленные хостингом в .local (pip --prefix .local),
//   2) при необходимости сам ставит зависимости из requirements.txt,
//   3) запускает Python-бота с правильным PYTHONPATH и передаёт его логи в консоль.
const { spawn, spawnSync } = require("child_process");
const fs = require("fs");
const path = require("path");

const CWD = __dirname;
const REQUIREMENTS = path.join(CWD, "requirements.txt");
const LOCAL_PREFIX = path.join(CWD, ".local");

// Находим рабочую команду Python.
function findPython() {
	for (const cmd of ["python3", "python"]) {
		const res = spawnSync(cmd, ["--version"], { stdio: "ignore" });
		if (!res.error && res.status === 0) return cmd;
	}
	return null;
}

// Собираем все каталоги site-packages внутри .local (туда ставит pip --prefix .local,
// который Pterodactyl запускает при старте, если задана переменная REQUIREMENTS_FILE).
function localSitePackages() {
	const dirs = [];
	const libRoot = path.join(LOCAL_PREFIX, "lib");
	if (fs.existsSync(libRoot)) {
		for (const entry of fs.readdirSync(libRoot)) {
			const sp = path.join(libRoot, entry, "site-packages");
			if (fs.existsSync(sp)) dirs.push(sp);
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

// Пробуем установить зависимости несколькими способами —
// на разных хостингах pip настроен по-разному.
function installDeps(py) {
	const variants = [
		// Так делает сам Pterodactyl при старте — самый надёжный вариант.
		["pip", ["install", "-U", "--no-cache-dir", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
		["pip3", ["install", "-U", "--no-cache-dir", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
		[py, ["-m", "pip", "install", "--prefix", LOCAL_PREFIX, "-r", REQUIREMENTS]],
		[py, ["-m", "pip", "install", "--user", "-r", REQUIREMENTS]],
		[py, ["-m", "pip", "install", "--user", "--break-system-packages", "-r", REQUIREMENTS]],
	];

	for (const [cmd, args] of variants) {
		console.log(`[launcher] Устанавливаю зависимости: ${cmd} ${args.join(" ")}`);
		const res = spawnSync(cmd, args, { cwd: CWD, stdio: "inherit" });
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

function main() {
	const py = findPython();
	if (!py) {
		console.error("[launcher] Python не найден (пробовал python3 и python).");
		console.log("[launcher] Повторная проверка через 30 секунд...");
		setTimeout(main, 30_000);
		return;
	}

	if (!depsReady(py)) {
		console.log("[launcher] Модуль aiogram не найден — ставлю зависимости...");
		installDeps(py);
	}

	if (!depsReady(py)) {
		console.error("[launcher] aiogram недоступен. В панели хостинга задайте переменную REQUIREMENTS_FILE=requirements.txt и перезапустите сервер.");
		console.log("[launcher] Повторная попытка через 60 секунд...");
		setTimeout(main, 60_000);
		return;
	}

	startBot(py);
}

main();
