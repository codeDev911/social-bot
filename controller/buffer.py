import asyncio
import requests

url = "https://api.buffer.com"

graphql_query_get_org = {
    "query": """
   query GetOrganizations {
  account {
    organizations {
      id
      name
    }
  }
}
    """
}

def get_channels_payload(org_id: str) -> dict:
    return {
        "query": """
            query GetChannels($orgId: OrganizationId!) {
                channels(input: { organizationId: $orgId }) {
                    id
                    name
                    service
                }
            }
        """,
        "variables": {
            "orgId": org_id  # Injected dynamically here
        }
    }


def get_profiles_graphql(api_key=None):
    try:
        # Use provided API key or fall back to environment variable
        token = api_key 
        
        if not token:
            print("❌ Buffer API key not found")
            return None
        
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        
        # FIrst we'll get the user's organization ID to use in the next query
        response_org = requests.post(url, json=graphql_query_get_org, headers=headers, timeout=30)
         # This will intercept 401 Unauthorized or 400 Bad Request before decoding
        response_org.raise_for_status() 
        
        data_org = response_org.json()
        organizations = data_org.get("data", {}).get("account", {}).get("organizations", []) or []
        if not organizations:
            print("❌ Buffer account has no organizations")
            return None

        # Read every organization, not just the first: a channel added to a
        # second organization used to stay invisible after re-connecting.
        channels = []
        seen_ids = set()
        for organization in organizations:
            org_id = organization.get("id")
            if not org_id:
                continue

            response = requests.post(url, json=get_channels_payload(org_id), headers=headers, timeout=30)

            # This will intercept 401 Unauthorized or 400 Bad Request before decoding
            response.raise_for_status() 

            data = response.json()

            # Check if GraphQL returned internal errors
            if "errors" in data:
                print("❌ Buffer API returned internal errors:")
                print(data["errors"])
                return None

            for channel in data.get("data", {}).get("channels", []) or []:
                channel_id = channel.get("id")
                if channel_id and channel_id not in seen_ids:
                    seen_ids.add(channel_id)
                    channels.append(channel)

        return channels

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP Error Occurred: {http_err}")
        body = http_err.response.text if http_err.response is not None else ""
        print(f"Raw Server Message: {body}")
        return None
    except Exception as e:
        print(f"❌ Python Script Error: {e}")
        return None


async def publish_instantly(profile_id, caption, video_url,api_key=None,post_type=None):
    """
    Sends a video directly to TikTok via Buffer GraphQL API instantly.
    
    :param profile_id: The ID string retrieved from your get_profiles() script
    :param caption: The text/hashtags for your TikTok post
    :param video_url: Public direct download URL to your mp4 asset file
    """
    
    if api_key is None:
        print("❌ Buffer API key is required for publishing.")
        return {"status_code":400, "error":"Buffer API key is required."}
 

    # doing filtering for facebook  upload
    facebook = {}
    if post_type :
        facebook={
            'metadata': {'facebook': {'type': post_type}}
        }

    # 📝 The GraphQL Mutation required for video posting 
    graphql_mutation = {
        "query": """
        mutation CreateInstantPost($input: CreatePostInput!) {
          createPost(input: $input) {
            ... on PostActionSuccess {
              post {
                id
                text
              }
            }
            ... on MutationError {
              message
            }
          }
        }
        """,
        "variables": {
            "input": {
                **facebook,
                "channelId": profile_id,
                "text": caption,
                "schedulingType": "automatic",
                "mode": "shareNow",  # ⚡ CRITICAL: Tells Buffer to skip queue and post immediately
                "assets": {
                    "video":{
                            "url": video_url
                        }
                },
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        # Send the GraphQL POST request without blocking the event loop
        response = await asyncio.to_thread(
            requests.post, url, json=graphql_mutation, headers=headers, timeout=60
        )
        response.raise_for_status()
        
        result = response.json()
        
        # Check for foundational GraphQL system errors
        if "errors" in result:
            print("❌ GraphQL Execution Error:")
            print(result["errors"])
            return {"status_code":400,"error":result["errors"]}


        # Parse Buffer mutation success response data
        post_data = result.get("data", {}).get("createPost", {})
        
        if "message" in post_data:
            print(f"❌ Buffer API Error: {post_data['message']}")
            return {"status_code":400,"error":post_data['message'], "post_id": post_data.get("post", {}).get("id")}
 
        return {"status_code":200, "post_id": post_data.get("post", {}).get("id")}

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP Exception Occurred: {http_err}")
        body = http_err.response.text if http_err.response is not None else str(http_err)
        print(f"Raw Server Traceback: {body}")
        return {"status_code":500, "error":body}
    except Exception as e:
        print(f"❌ Script Runtime Error: {e}")
        return {"status_code":500, "error":str(e)}

