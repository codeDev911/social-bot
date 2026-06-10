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
           <!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Video Processing Update</title>
</head>
<body style="margin: 0; padding: 0; background-color: #f6f9fc; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; -webkit-font-smoothing: antialiased;">
    <table border="0" cellpadding="0" cellspacing="0" width="100%" style="background-color: #f6f9fc; padding: 40px 20px;">
        <tr>
            <td align="center">
                <!-- Main Email Card -->
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 540px; background-color: #ffffff; border-radius: 12px; box-shadow: 0 4px 12px rgba(0, 0, 0, 0.05); overflow: hidden;">
                    
                    <!-- Header Banner -->
                    <tr>
                        <td style="padding: 32px 32px 20px 32px;">
                            <h2 style="margin: 0 0 8px 0; color: #1a1f36; font-size: 22px; font-weight: 700; letter-spacing: -0.5px;">Video Processing Update</h2>
                            <p style="margin: 0; color: #4f566b; font-size: 15px; line-height: 1.5;">Your video request has been processed. Here are the details:</p>
                        </td>
                    </tr>

                    <!-- Data Info Section -->
                    <tr>
                        <td style="padding: 0 32px 24px 32px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border-collapse: collapse;">
                                
                                <!-- Video Name -->
                                <tr style="border-bottom: 1px solid #eff2f5;">
                                    <td style="padding: 14px 0; color: #718096; font-size: 14px; font-weight: 500; width: 30%;">Video Name</td>
                                    <td style="padding: 14px 0; color: #1a202c; font-size: 14px; font-weight: 600; text-align: right;">{video_name}</td>
                                </tr>

                                <!-- Title -->
                                <tr style="border-bottom: 1px solid #eff2f5;">
                                    <td style="padding: 14px 0; color: #718096; font-size: 14px; font-weight: 500;">Title</td>
                                    <td style="padding: 14px 0; color: #1a202c; font-size: 14px; font-weight: 500; text-align: right;">{title}</td>
                                </tr>

                                <!-- Size -->
                                <tr style="border-bottom: 1px solid #eff2f5;">
                                    <td style="padding: 14px 0; color: #718096; font-size: 14px; font-weight: 500;">Size</td>
                                    <td style="padding: 14px 0; color: #1a202c; font-size: 14px; font-weight: 500; text-align: right;">{size}</td>
                                </tr>

                                <!-- Triggered At -->
                                <tr style="border-bottom: 1px solid #eff2f5;">
                                    <td style="padding: 14px 0; color: #718096; font-size: 14px; font-weight: 500;">Triggered At</td>
                                    <td style="padding: 14px 0; color: #1a202c; font-size: 14px; font-weight: 500; text-align: right;">{triggered_at}</td>
                                </tr>

                                <!-- Video Status -->
                                <tr>
                                    <td style="padding: 16px 0 10px 0; color: #718096; font-size: 14px; font-weight: 500;">Status</td>
                                    <td style="padding: 16px 0 10px 0; text-align: right;">
                                        <span style="background-color: #e6fffa; color: #047481; padding: 6px 12px; border-radius: 20px; font-size: 12px; font-weight: 700; display: inline-block; text-transform: uppercase; letter-spacing: 0.5px;">{video_status}</span>
                                    </td>
                                </tr>

                            </table>
                        </td>
                    </tr>

                    <!-- Footer/Sign-off inside the card -->
                    <tr>
                        <td style="padding: 0 32px 32px 32px;">
                            <table border="0" cellpadding="0" cellspacing="0" width="100%" style="border-top: 1px solid #e3e8ee; padding-top: 20px;">
                                <tr>
                                    <td style="color: #697386; font-size: 13px; line-height: 1.5;">
                                        Thank you for using our service!<br>
                                        <span style="color: #a3acb9; font-size: 12px;">This is an automated notification.</span>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>

                </table>
                
                <!-- Extra space or subtle outer footer if needed -->
                <table border="0" cellpadding="0" cellspacing="0" width="100%" style="max-width: 540px; margin-top: 20px;">
                    <tr>
                        <td align="center" style="color: #94a3b8; font-size: 12px;">
                            © 2026 Your Company Name. All rights reserved.
                        </td>
                    </tr>
                </table>

            </td>
        </tr>
    </table>
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


def build_scheduled_videos_email(
    user_name: str,
    default_upload_dest: str,
    videos: list
    ):
    video_cards = ""


    for video in videos:
        platform_color = {
            "youtube": "#FF0000",
            "tiktok": "#000000",
            "instagram": "#E1306C",
            "facebook": "#1877F2"
        }.get(video.get("platform", "").lower(), "#6B7280")

        video_cards += f"""
        <tr>
            <td style="padding:0 30px 20px 30px;">
                <table width="100%" cellpadding="0" cellspacing="0"
                    style="border:1px solid #E5E7EB;border-radius:12px;background:#FFFFFF;">

                    <tr>
                        <td style="padding:20px;">

                            <div style="
                                display:inline-block;
                                background:{platform_color};
                                color:white;
                                padding:6px 12px;
                                border-radius:20px;
                                font-size:12px;
                                font-weight:bold;
                                margin-bottom:12px;">
                                {video["platform"].upper()}
                            </div>

                            <h3 style="
                                margin:0 0 12px 0;
                                font-size:18px;
                                color:#111827;
                                line-height:26px;">
                                {video["title"]}
                            </h3>

                            <table width="100%">
                                <tr>
                                    <td style="padding:6px 0;color:#6B7280;width:120px;">
                                        Quality Score
                                    </td>
                                    <td style="padding:6px 0;color:#111827;">
                                        {video["score"]}
                                    </td>
                                </tr>

                                <tr>
                                    <td style="padding:6px 0;color:#6B7280;">
                                        Video ID
                                    </td>
                                    <td style="padding:6px 0;color:#111827;">
                                        {video["externalId"]}
                                    </td>
                                </tr>

                                <tr>
                                    <td style="padding:6px 0;color:#6B7280;">
                                        Destination
                                    </td>
                                    <td style="padding:6px 0;color:#111827;">
                                        {default_upload_dest}
                                    </td>
                                </tr>
                            </table>

                            <div style="margin-top:18px;">
                                <a href="{video['url']}"
                                style="
                                        background:#111827;
                                        color:#ffffff;
                                        text-decoration:none;
                                        padding:12px 18px;
                                        border-radius:8px;
                                        display:inline-block;
                                        font-weight:600;">
                                    View Original Video
                                </a>
                            </div>

                        </td>
                    </tr>

                </table>
            </td>
        </tr>
        """

    return f"""
    

    <!DOCTYPE html>

    <html>
    <head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Videos Scheduled</title>
    </head>

    <body style="
        margin:0;
        padding:0;
        background:#F3F4F6;
        font-family:Arial,Helvetica,sans-serif;">

    <table width="100%" cellpadding="0" cellspacing="0">
    <tr>
    <td align="center" style="padding:40px 20px;">

    <table width="700" cellpadding="0" cellspacing="0"
        style="
            background:#ffffff;
            border-radius:18px;
            overflow:hidden;
            box-shadow:0 10px 35px rgba(0,0,0,0.08);">

    
    <tr>
        <td style="
            background:#111827;
            padding:50px 40px;
            text-align:center;">

            <h1 style="
                margin:0;
                color:white;
                font-size:32px;">
                New Videos Scheduled
            </h1>

            <p style="
                margin-top:15px;
                color:#D1D5DB;
                font-size:16px;
                line-height:26px;">
                Fresh content matching your topics has been discovered and added to your publishing queue.
            </p>

        </td>
    </tr>

    <tr>
        <td style="padding:35px 30px;">

            <p style="
                margin:0 0 15px;
                color:#374151;
                font-size:16px;">
                Hello {user_name},
            </p>

            <p style="
                margin:0;
                color:#6B7280;
                font-size:15px;
                line-height:26px;">
                We discovered <strong>{len(videos)}</strong> new videos and successfully scheduled them for publishing to:
            </p>

            <div style="
                margin-top:20px;
                background:#F9FAFB;
                border:1px solid #E5E7EB;
                padding:18px;
                border-radius:10px;">

                <strong style="color:#111827;">
                    {default_upload_dest}
                </strong>

            </div>

        </td>
    </tr>

    {video_cards}

    <tr>
        <td style="
            padding:30px;
            background:#F9FAFB;
            border-top:1px solid #E5E7EB;
            text-align:center;">

            <p style="
                margin:0;
                color:#6B7280;
                font-size:13px;">
                This notification was generated automatically by our content automation platform.
            </p>

        </td>
    </tr>
    

    </table>

    </td>
    </tr>
    </table>

    </body>
    </html>
    """


def new_video_scheduled_mail(recipient, videos,user_name, default_upload_dest):
    subject = "New Video Scheduled Successfully"
    destinations = ", ".join(default_upload_dest)
    body = build_scheduled_videos_email(
    user_name=user_name,
    default_upload_dest=destinations,
    videos=videos
    )
    return send_email(subject, body, recipient)




