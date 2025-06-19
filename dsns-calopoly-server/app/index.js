// Express and Socket.io Server (CommonJS version)
const express = require("express");
const http = require("http");
const { Server } = require("socket.io");
const cors = require("cors");

const rooms = {};
const disconnectTimers = {};

const app = express();
app.use(cors());
const server = http.createServer(app);
const io = new Server(server, {
	cors: { origin: "*", methods: ["GET", "POST"] }
});

const isValidJSON = (msg) => {
	try {
		JSON.parse(msg);
		return true;
	} catch {
		return false;
	}
};

io.on("connection", (socket) => {
	console.log(`Client connected: ${socket.id}`);

	socket.on("join-room", (roomID, player) => {
		if (!roomID || !player) return;

		socket.join(roomID);
		console.log(`${player.name} joined ${roomID}`);

		if (rooms[roomID] && rooms[roomID].players.find((p) => p.name === player.name && p.id !== player.id)) {
			socket.emit("name-taken", true);
			return;
		}

		if (!rooms[roomID]) {
			rooms[roomID] = { players: [], currentTurn: 0, dice: [-1, -1], started: false, chat: [] };
		}

		player.socketID = socket.id;
		player.roomID = roomID;

		const existingPlayer = rooms[roomID].players.find((p) => p.id === player.id);
		if (!existingPlayer) {
			const possibleSymbols = ["🐇", "🐄", "🐕", "🐈", "🐓", "🐖", "🐐", "🐎", "🐂", "🐑", "🐩"];
			const usedSymbols = rooms[roomID].players.map((p) => p.symbol);
			player.symbol = possibleSymbols.find((s) => !usedSymbols.includes(s)) || "🐇";
			rooms[roomID].players.push(player);
		}

		io.to(roomID).emit("joined-room", JSON.stringify(rooms[roomID]));
		io.to(roomID).emit("update-players", JSON.stringify(rooms[roomID].players));
		io.to(roomID).emit("update-turn", JSON.stringify(rooms[roomID].currentTurn));
		io.to(roomID).emit("update-dice", JSON.stringify(rooms[roomID].dice));
		io.to(roomID).emit("update-chat", JSON.stringify(rooms[roomID].chat));
	});

	socket.on("update-players", (roomID, msg) => {
		if (!isValidJSON(msg) || !rooms[roomID]) return;
		const players = JSON.parse(msg);
		if (Array.isArray(players)) {
			rooms[roomID].players = players;
			io.to(roomID).emit("update-players", JSON.stringify(players));
			console.log(JSON.stringify(players));
		}
	});

	socket.on("update-turn", (roomID, msg) => {
		io.to(roomID).emit("update-turn", msg);
	});

	socket.on("update-dice", (roomID, msg) => {
		io.to(roomID).emit("update-dice", msg);
	});

	socket.on("start-state", (roomID, msg) => {
		if (!isValidJSON(msg) || !rooms[roomID]) return;
		const started = JSON.parse(msg);
		if (typeof started === "boolean") {
			rooms[roomID].started = started;
			io.to(roomID).emit("start-state", msg);
		}
	});

	socket.on("chat", (roomID, msg) => {
		if (!rooms[roomID] || typeof msg !== "string" || msg.length > 500) return;
		rooms[roomID].chat.push(msg);
		io.to(roomID).emit("chat", JSON.stringify(rooms[roomID].chat));
	});

	socket.on("disconnect", () => {
		console.log(`Client disconnected: ${socket.id}`);

		for (const roomID in rooms) {
			const room = rooms[roomID];
			const playerIndex = room.players.findIndex((p) => p.socketID === socket.id);
			if (playerIndex !== -1) {
				const player = room.players[playerIndex];

				console.log(`${player.name} disconnected from ${roomID}. Waiting 60 seconds...`);
				if (rooms[roomID]) {
					rooms[roomID].chat.push(`${player.name} disconnected. Waiting 60 seconds...`);
					io.to(roomID).emit("chat", JSON.stringify(rooms[roomID].chat));
				}

				player.disconnected = true;

				disconnectTimers[socket.id] = setTimeout(() => {
					const updatedIndex = room.players.findIndex((p) => p.socketID === socket.id);
					if (updatedIndex !== -1 && room.players[updatedIndex].disconnected) {
						room.players.splice(updatedIndex, 1);
						if (room.players.length === 0) delete rooms[roomID];
						io.to(roomID).emit("update-players", JSON.stringify(room.players));

						if (rooms[roomID]) {
							rooms[roomID].chat.push(`${player.name} disconnected.`);
							io.to(roomID).emit("chat", JSON.stringify(rooms[roomID].chat));
						}
					}
				}, 60 * 1000);
				break;
			}
		}
	});
});

server.listen(4000, () => console.log("Server running on port 4000"));