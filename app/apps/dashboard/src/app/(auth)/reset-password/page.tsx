import { Button } from "@ui/components/ui/button";
import { ChevronLeftIcon } from "lucide-react";
import Image from "next/image";
import Link from "next/link";
import { Suspense } from "react";
import ForgotPasswordForm from "@/components/forgot-password-form";
import { Logo } from "@/components/logo";
import ResetPasswordForm from "@/components/reset-password-form";

export default function Page() {
	return (
		<div className="grid grid-cols-2">
			<div className="flex h-screen w-full flex-col gap-4 p-8">
				<div className="flex justify-between">
					<Logo className="size-8 rounded-full" />
					<Link href="/sign-in">
						<Button variant={"ghost"} className="">
							<ChevronLeftIcon />
							Back to Sign in
						</Button>
					</Link>
				</div>
				<Image
					src={"/cover4.png"}
					width={1600}
					height={900}
					alt="Login Image"
					className="h-screen w-full rounded-lg object-cover"
				/>
			</div>
			<Suspense>
				<ResetPasswordForm />
			</Suspense>
		</div>
	);
}
