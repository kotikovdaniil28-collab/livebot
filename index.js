// Запускатель для хостингов с ядром «node.js + python3» (Pterodactyl/Play2GO).
// Панель всегда стартует через Node и ищет index.js — этот файл просто
// запускает нашего Python-бота и передаёт его логи в консоль.
const { spawn } = require("child_process");

function start() {
	console.log("[launcher] Запускаю Python-бота: python3 main.py");
	const bot = spawn("python3", ["main.py"], {
		cwd: __dirname,
		stdio: "inherit",
	});

	bot.on("exit", (code) => {
		console.log(`[launcher] Бот завершился с кодом ${code}.`);
		if (code !== 0) {
			console.log("[launcher] Перезапуск через 10 секунд...");
			setTimeout(start, 10_000);
		} else {
			process.exit(0);
		}
	});

	bot.on("error", (err) => {
		console.error("[launcher] Не удалось запустить python3:", err.message);
		console.log("[launcher] Пробую команду 'python' вместо 'python3'...");
		const fallback = spawn("python", ["main.py"], { cwd: __dirname, stdio: "inherit" });
		fallback.on("exit", (code) => process.exit(code ?? 1));
	});
}

start();
