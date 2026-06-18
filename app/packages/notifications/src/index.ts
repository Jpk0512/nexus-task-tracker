export const sendNotification = async (..._args: unknown[]): Promise<void> => {
	if (process.env.NODE_ENV === "development") {
		console.log("[notifications] sendNotification (stub) called");
	}
};

export const notify = async (..._args: unknown[]): Promise<void> => {
	if (process.env.NODE_ENV === "development") {
		console.log("[notifications] notify (stub) called");
	}
};
