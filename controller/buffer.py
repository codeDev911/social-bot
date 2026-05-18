import requests
 
url = "https://api.buffer.com"

graphql_query = {
    "query": """
    query GetChannels {
      account {
        channels {
          id
          name
          service
        }
      }
    }
    """
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
        
        # Note: GraphQL always requires a POST request
        response = requests.post(url, json=graphql_query, headers=headers)
        
        # This will intercept 401 Unauthorized or 400 Bad Request before decoding
        response.raise_for_status() 
        
        data = response.json()
        
        # Check if GraphQL returned internal errors
        if "errors" in data:
            print("❌ Buffer API returned internal errors:")
            print(data["errors"])
            return None

        channels = data.get("data", {}).get("account", {}).get("channels", [])
        
      
        
        return channels

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP Error Occurred: {http_err}")
        print(f"Raw Server Message: {response.text}")
        return None
    except Exception as e:
        print(f"❌ Python Script Error: {e}")
        return None


def publish_instantly(profile_id, caption, video_url,api_key=None):
    """
    Sends a video directly to TikTok via Buffer GraphQL API instantly.
    
    :param profile_id: The ID string retrieved from your get_profiles() script
    :param caption: The text/hashtags for your TikTok post
    :param video_url: Public direct download URL to your mp4 asset file
    """
    
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
                "channelId": tiktok_profile_id,
                "text": caption,
                "schedulingType": "automatic",
                "mode": "shareNow",  # ⚡ CRITICAL: Tells Buffer to skip queue and post immediately
                "assets": {
                    "videos": [
                        {
                            "url": video_url
                        }
                    ]
                }
            }
        }
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    try:
        # Send the GraphQL POST request
        response = requests.post(url, json=graphql_mutation, headers=headers)
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
            print(f"❌ Buffer Business Error: {post_data['message']}")
            return {"status_code":400,"error":post_data['message'], "post_id": post_data.get("post", {}).get("id")}

            
        print("🎉 Video successfully routed directly to TikTok!")
        print(f"Assigned Buffer Post ID: {post_data.get('post', {}).get('id')}")
        return {"status_code":200, "post_id": post_data.get("post", {}).get("id")}

    except requests.exceptions.HTTPError as http_err:
        print(f"❌ HTTP Exception Occurred: {http_err}")
        print(f"Raw Server Traceback: {response.text}")
    except Exception as e:
        print(f"❌ Script Runtime Error: {e}")
    return False

