"use client";
import { t } from "@nexus-app/locale";
import { DEFAULT_LOCALE, LOCALES } from "@nexus-app/locale/constants";
import { getTimezones } from "@nexus-app/locale/timezones";
import { Button } from "@nexus-app/ui/button";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
} from "@nexus-app/ui/command";
import {
	Form,
	FormControl,
	FormDescription,
	FormField,
	FormItem,
	FormLabel,
	FormMessage,
} from "@nexus-app/ui/form";
import { Input } from "@nexus-app/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@nexus-app/ui/popover";
import {
	Select,
	SelectContent,
	SelectItem,
	SelectTrigger,
	SelectValue,
} from "@nexus-app/ui/select";
import { Textarea } from "@nexus-app/ui/textarea";
import { generateTeamSlug } from "@nexus-app/utils/teams";
import { PopoverClose } from "@radix-ui/react-popover";
import { useMutation } from "@tanstack/react-query";
import { ChevronDown, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import z from "zod";
import { useUser } from "@/components/user-provider";
import { useScopes } from "@/hooks/use-scopes";
import { useTeamParams } from "@/hooks/use-team-params";
import { useZodForm } from "@/hooks/use-zod-form";
import { cn } from "@/lib/utils";
import { queryClient, trpc } from "@/utils/trpc";

export const teamFormSchema = z.object({
	id: z.string().optional(),
	name: z
		.string()
		.min(2, "Team name must be at least 2 characters")
		.max(50, "Team name must be at most 50 characters"),
	slug: z
		.string()
		.min(2, "Slug must be at least 2 characters")
		.max(30, "Slug must be at most 30 characters"),
	email: z.string().email("Invalid email address"),
	description: z
		.string()
		.max(500, "Description must be at most 500 characters")
		.optional(),
	locale: z.string().optional(),
	timezone: z.string().optional(),
});

export const TeamForm = ({
	defaultValues,
	scrollarea = true,
}: {
	defaultValues?: Partial<z.infer<typeof teamFormSchema>>;
	scrollarea?: boolean;
}) => {
	const { setParams } = useTeamParams();
	const [openTimezone, setOpenTimezone] = useState(false);
	const user = useUser();
	const canWriteTeam = useScopes(["team:write"]) && !!defaultValues?.id;
	const form = useZodForm(teamFormSchema, {
		defaultValues: {
			name: "",
			email: user?.email || "",
			description: "",
			slug: "",
			locale: DEFAULT_LOCALE,
			...defaultValues,
		},
		disabled: !canWriteTeam && !!defaultValues?.id,
	});

	const name = form.watch("name");

	useEffect(() => {
		if (!defaultValues?.id) {
			const slug = generateTeamSlug(name || "");
			form.setValue("slug", slug);
		}
	}, [name]);

	useEffect(() => {
		form.setValue("email", user?.email || "");
	}, [user?.email]);

	const { mutateAsync: switchTeam } = useMutation(
		trpc.users.switchTeam.mutationOptions(),
	);

	const { mutateAsync: createTeam, isPending: isCreating } = useMutation(
		trpc.teams.create.mutationOptions({
			onSuccess: async (team) => {
				setParams(null);
				await switchTeam({ slug: team.slug });
				window.location.href = `/team/${team.slug}/onboarding`;
			},
		}),
	);

	const { mutateAsync: updateTeam, isPending: isUpdating } = useMutation(
		trpc.teams.update.mutationOptions({
			onSuccess: () => {
				queryClient.invalidateQueries(trpc.teams.getCurrent.queryOptions());
				queryClient.invalidateQueries(trpc.users.getCurrent.queryOptions());
				toast.success("Team updated successfully");
			},
		}),
	);

	const handleSubmit = async (data: z.infer<typeof teamFormSchema>) => {
		if (data.id) {
			// Update existing team
			await updateTeam({
				...data,
				id: data.id,
			});
		} else {
			const team = await createTeam({
				...data,
			});

			// Create new team
		}
	};

	return (
		<Form {...form}>
			<form onSubmit={form.handleSubmit(handleSubmit)} className="space-y-4">
				{/* <ScrollArea className={scrollarea ? "h-[calc(100vh-140px)]" : ""}> */}
				<div className={cn("space-y-4")}>
					<FormField
						control={form.control}
						name="name"
						render={({ field }) => (
							<FormItem>
								<FormLabel>{t("forms.teamForm.name.label")}</FormLabel>
								<FormControl>
									<Input placeholder="ACME" {...field} />
								</FormControl>
								<FormMessage />
							</FormItem>
						)}
					/>

					{!defaultValues?.id && (
						<FormField
							control={form.control}
							name="slug"
							render={({ field }) => (
								<FormItem>
									<FormLabel>URL</FormLabel>
									<FormControl>
										<div className="relative">
											<div className="pointer-events-none absolute top-0 left-0 flex h-full items-center pl-3 text-muted-foreground text-sm">
												http://localhost:5179/
											</div>
											<Input
												placeholder="acme"
												{...field}
												value={field.value || ""}
												className="pl-[141px]"
											/>
										</div>
									</FormControl>
									<FormDescription>
										{t("forms.teamForm.slug.description")}
									</FormDescription>
									<FormMessage />
								</FormItem>
							)}
						/>
					)}

					<FormField
						control={form.control}
						name="email"
						render={({ field }) => (
							<FormItem>
								<FormLabel>{t("forms.teamForm.email.label")}</FormLabel>
								<FormControl>
									<Input placeholder="acme@example.com" {...field} />
								</FormControl>
								<FormMessage />
							</FormItem>
						)}
					/>

					<FormField
						control={form.control}
						name="locale"
						render={({ field }) => (
							<FormItem>
								<FormLabel>{t("forms.teamForm.locale.label")}</FormLabel>
								<FormControl>
									<Select value={field.value} onValueChange={field.onChange}>
										<SelectTrigger className="w-full">
											<SelectValue
												placeholder={t("forms.teamForm.locale.placeholder")}
												{...field}
											/>
										</SelectTrigger>
										<SelectContent>
											{LOCALES.map((locale) => (
												<SelectItem key={locale.code} value={locale.code}>
													{locale.name}
												</SelectItem>
											))}
										</SelectContent>
									</Select>
								</FormControl>
								<FormMessage />
							</FormItem>
						)}
					/>

					<FormField
						control={form.control}
						name="timezone"
						render={({ field }) => (
							<FormItem>
								<FormLabel>{t("forms.teamForm.timezone.label")}</FormLabel>
								<FormControl>
									<Popover open={openTimezone} onOpenChange={setOpenTimezone}>
										<PopoverTrigger className="w-full" asChild>
											{/* <SelectValue placeholder="Select a timezone" {...field} /> */}
											<Button
												type="button"
												variant="outline"
												className="w-full justify-between"
											>
												{getTimezones().find((tz) => tz.tzCode === field.value)
													?.name || (
													<span className="text-muted-foreground">
														{t("forms.teamForm.timezone.placeholder")}
													</span>
												)}
												<ChevronDown className="ml-2 size-4 text-muted-foreground" />
											</Button>
										</PopoverTrigger>
										<PopoverContent align="start" className="w-92">
											<Command>
												<CommandInput placeholder="Search timezone..." />
												<CommandEmpty>No timezone found.</CommandEmpty>
												<CommandGroup>
													{getTimezones().map((tz) => (
														<CommandItem
															value={tz.tzCode}
															key={tz.tzCode}
															onSelect={() => {
																field.onChange(tz.tzCode);
																setOpenTimezone(false);
															}}
														>
															{tz.name}
														</CommandItem>
													))}
												</CommandGroup>
											</Command>
										</PopoverContent>
									</Popover>
								</FormControl>
								<FormMessage />
							</FormItem>
						)}
					/>

					{defaultValues?.id && (
						<FormField
							control={form.control}
							name="description"
							render={({ field }) => (
								<FormItem>
									<FormLabel>{t("forms.teamForm.description.label")}</FormLabel>
									<FormControl>
										<Textarea
											placeholder={t("forms.teamForm.description.placeholder")}
											className="min-h-[200px]"
											{...field}
										/>
									</FormControl>
									<FormMessage />
								</FormItem>
							)}
						/>
					)}
				</div>
				{/* </ScrollArea> */}
				{(canWriteTeam || !defaultValues?.id) && !defaultValues?.id && (
					<div className="flex items-center justify-end px-4">
						<Button type="submit" disabled={isUpdating || isCreating}>
							{(isUpdating || isCreating) && (
								<Loader2 className="animate-spin" />
							)}
							Save
						</Button>
					</div>
				)}
				{/* Sticky save bar — only on the edit-existing-team path. Appears when
				    the form is dirty, slides up from the bottom of the viewport.
				    Aligned with the dashboard content column (sidebar offset is set
				    by main layout, so we use a translucent surface + safe-area
				    padding so it reads as a footer rather than a modal). */}
				{canWriteTeam && defaultValues?.id && (
					<StickySaveBar
						isDirty={form.formState.isDirty}
						isSubmitting={isUpdating}
						onDiscard={() =>
							form.reset({
								name: defaultValues?.name ?? "",
								email: defaultValues?.email ?? user?.email ?? "",
								description: defaultValues?.description ?? "",
								slug: defaultValues?.slug ?? "",
								locale: defaultValues?.locale ?? DEFAULT_LOCALE,
								timezone: defaultValues?.timezone ?? undefined,
								id: defaultValues?.id,
							})
						}
					/>
				)}
			</form>
		</Form>
	);
};

// Linear-style "unsaved changes" footer. Fixed to the bottom of the viewport,
// inset left to clear the sidebar (var(--sidebar-width) when available,
// defaults to 0 for unauth/marketing surfaces).
function StickySaveBar({
	isDirty,
	isSubmitting,
	onDiscard,
}: {
	isDirty: boolean;
	isSubmitting: boolean;
	onDiscard: () => void;
}) {
	if (!isDirty) return null;
	return (
		<div
			role="region"
			aria-label="Unsaved changes"
			className={cn(
				"fixed right-0 bottom-0 left-0 z-40",
				"border-border border-t bg-popover/95 backdrop-blur",
				"slide-in-from-bottom-2 fade-in animate-in duration-150",
			)}
			style={{
				paddingLeft: "var(--sidebar-width, 0px)",
			}}
		>
			<div className="flex items-center justify-between gap-3 px-6 py-3">
				<p className="text-[13px] text-muted-foreground">
					You have unsaved changes
				</p>
				<div className="flex items-center gap-2">
					<Button
						type="button"
						variant="ghost"
						size="sm"
						onClick={onDiscard}
						disabled={isSubmitting}
					>
						Discard
					</Button>
					<Button type="submit" size="sm" disabled={isSubmitting}>
						{isSubmitting && <Loader2 className="size-3 animate-spin" />}
						Save changes
					</Button>
				</div>
			</div>
		</div>
	);
}
