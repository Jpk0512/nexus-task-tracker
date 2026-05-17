import { getEmailUrl } from "@mimir/utils/envs";
import {
	Body,
	Container,
	Heading,
	Preview,
	Section,
	Text,
} from "@react-email/components";
import { Footer } from "../components/footer";
import { Logo } from "../components/logo";
import {
	Button,
	EmailThemeProvider,
	getEmailInlineStyles,
	getEmailThemeClasses,
} from "../components/theme";

interface Props {
	email: string;
	token: string;
}

const baseUrl = getEmailUrl();

export const ResetPasswordEmail = ({
	email = "jhondoe@example.com",
	token = "",
}: Props) => {
	const text = `You've requested a password reset.`;
	const themeClasses = getEmailThemeClasses();
	const lightStyles = getEmailInlineStyles("light");

	return (
		<EmailThemeProvider preview={<Preview>{text}</Preview>}>
			<Body
				className={`mx-auto my-auto font-sans ${themeClasses.body}`}
				style={lightStyles.body}
			>
				<Container
					className={`mx-auto my-[40px] max-w-[600px] p-[20px] ${themeClasses.container}`}
					style={{
						borderStyle: "solid",
						borderWidth: 1,
						borderColor: lightStyles.container.borderColor,
					}}
				>
					<Logo />
					<Heading
						className={`mx-0 my-[30px] p-0 text-center font-normal text-[21px] ${themeClasses.heading}`}
						style={{ color: lightStyles.text.color }}
					>
						You've requested a password reset.
					</Heading>

					<br />

					<span
						className={`font-medium ${themeClasses.text}`}
						style={{ color: lightStyles.text.color }}
					>
						{"Hello,"}
					</span>
					<Text
						className={themeClasses.text}
						style={{ color: lightStyles.text.color }}
					>
						You've requested a password reset on Mimir. To proceed, please click
						the button below.
					</Text>

					<Text
						className={themeClasses.mutedText}
						style={{ color: lightStyles.mutedText.color }}
					>
						Best regards, founder
					</Text>

					<br />

					<Section className="text-center">
						<Button href={`${baseUrl}/reset-password?token=${token}`}>
							Reset Password
						</Button>
					</Section>

					<br />
					<br />
					<br />

					<Footer />
				</Container>
			</Body>
		</EmailThemeProvider>
	);
};

export default ResetPasswordEmail;
