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
	teamName: string;
	title: string;
	message: string;
	ctaLink?: string;
}

export const NotificationEmail = ({
	teamName = "Acme Inc.",
	title = "New Task Assigned",
	message = "You have been assigned a new task.",
	ctaLink = "#",
}: Props) => {
	const text = `You've been invited to ${teamName}.`;
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
						{title}
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
						{message}
					</Text>

					<Text
						className={themeClasses.mutedText}
						style={{ color: lightStyles.mutedText.color }}
					>
						This is an automated notification for the team {teamName}.
						<br />
						Do not reply to this email.
					</Text>

					<br />
					{ctaLink && (
						<Section className="text-center">
							<Button href={`${ctaLink}`}>View Details</Button>
						</Section>
					)}
					<br />
					<br />
					<br />

					<Footer />
				</Container>
			</Body>
		</EmailThemeProvider>
	);
};

export default NotificationEmail;
