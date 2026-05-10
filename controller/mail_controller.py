import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Configuration for the email sender
SMTP_SERVER = 'smtp.gmail.com'
SMTP_PORT = 587
EMAIL_ADDRESS = 'afnanahhad@gmail.com'
EMAIL_PASSWORD = 'qqyp akpi jein zrlj'

def html_content(video_name, title, size, triggered_at, video_status):
    return  f"""
            <html>
                <body style="font-family: Arial, sans-serif; line-height: 1.6;">
                    <h2 style="color: #4CAF50;">Video Processing Update</h2>
                    <p>Here are the details of the video:</p>
                    <table style="border-collapse: collapse; width: 100%; max-width: 600px;">
                        <tr>
                            <th style="text-align: left; padding: 8px; background-color: #f2f2f2; border: 1px solid #ddd;">Field</th>
                            <th style="text-align: left; padding: 8px; background-color: #f2f2f2; border: 1px solid #ddd;">Details</th>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">Video Name</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{video_name}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">Title</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{title}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">Size</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{size}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">Triggered At</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{triggered_at}</td>
                        </tr>
                        <tr>
                            <td style="padding: 8px; border: 1px solid #ddd;">Video Status</td>
                            <td style="padding: 8px; border: 1px solid #ddd;">{video_status}</td>
                        </tr>
                    </table>
                    <p style="margin-top: 20px;">Thank you for using our service!</p>
                </body>
            </html>
            """


def send_email(subject, body, recipient):
    try:
        # Create the email
        msg = MIMEMultipart()
        msg['From'] = EMAIL_ADDRESS
        msg['To'] = recipient
        msg['Subject'] = subject
        # Attach the HTML content as the email body
        msg.attach(MIMEText(body, 'html'))

        # Connect to the SMTP server and send the email
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.starttls()
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.send_message(msg)
        print("Email sent successfully")
        return f"Email sent to {recipient}"
    except Exception as e:
        print("Failed to send email:", e)
        return f"Failed to send email: {e}"
 
def trigger_uploaded(recipient, file_name, title, size, triggered_at, video_status):
    subject = "Uploaded Triggered Successfully"
    body = html_content(file_name, title, size, triggered_at, video_status)
    return send_email(subject, body, recipient)

def trigger_deleted(recipient, file_name, title, size, triggered_at, video_status):
    subject = "Deleted Triggered Successfully"
    body = html_content(file_name, title, size, triggered_at, video_status)
    return send_email(subject, body, recipient)