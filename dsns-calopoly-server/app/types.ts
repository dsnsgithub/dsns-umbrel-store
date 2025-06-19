export interface Player {
	position: number;
	name: string;
	symbol: string;
	money: number;
	properties: Property[];
	recentDiff: number;
	id: string;
	socketID: string;
	roomID?: string;
	disconnected?: boolean;
}

export interface Property {
	name: string;
	type: "attraction" | "property" | "utility" | "special" | "tax";
	price?: number;
	houses?: number;
	mortgaged?: boolean;
	hotel?: boolean;
	color?: string;
	ownerID?: string;
	rent?: number[];
}

export interface GameState {
	players: Player[];
	currentTurn: number;
	dice: [number, number];
	started: boolean;
	chat: string[];
}