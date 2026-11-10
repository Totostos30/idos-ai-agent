// Точка входа: один фронтенд на три окна — остров (island), главное окно (main / app) и выбор области (select).
import { view } from "./shell.js";
import { core } from "./rpc.js";
import { mountIsland } from "./island.js";
import { mountApp } from "./app.js";
import { mountSelect } from "./select.js";

if (view === "main" || view === "app") mountApp();
else if (view === "select") mountSelect();
else mountIsland();


window.idos = { core, view };
