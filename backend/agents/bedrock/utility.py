import json
import boto3
from termcolor import colored
from rich.console import Console
from rich.markdown import Markdown
import boto3
import copy
import uuid

# session = boto3.session.Session()
session = boto3.Session(profile_name='ashu.joshi')

# region = session.region_name

# Runtime Endpoints
# bedrock_agents_runtime = boto3.client("bedrock-agent-runtime", region_name=region)
bedrock_agents_runtime = session.client("bedrock-agent-runtime")


def invoke_agent_with_roc(
    actionGroups, agent_instruction, model_id, inputText, tool_list
):

    # start a new session
    sessionId = str(uuid.uuid4())
    agent_answer = str()

    # prepare request parameters before invoking inline agent
    request_params = {
        "actionGroups": actionGroups,
        "instruction": agent_instruction,
        "foundationModel": model_id,
        "sessionId": sessionId,
        "endSession": False,
        "enableTrace": True,
    }

    request_params["inputText"] = inputText
    print(f"Got input: {inputText}")
    agent_answer, roc = invoke_inline_agent_helper(request_params)
    print(f"Got agent answer: {agent_answer}")

    while not agent_answer:
        # prepare request parameters before invoking inline agent
        request_params = {
            "actionGroups": actionGroups,
            "instruction": agent_instruction,
            "foundationModel": model_id,
            "sessionId": sessionId,
            "endSession": False,
            "enableTrace": True,
            "inlineSessionState": process_roc(
                roc, tool_list
            ),  # process return of control
        }
        request_params["inputText"] = ""
        agent_answer, roc = invoke_inline_agent_helper(request_params)
        print(f"Got agent answer: {agent_answer}")

    return agent_answer


def process_roc(roc, tool_list):
    inlineSessionState = {"returnControlInvocationResults": []}
    inlineSessionState["invocationId"] = roc["invocationId"]

    for invocationInput in roc["invocationInputs"]:
        functionInvocationInput = copy.deepcopy(
            invocationInput["functionInvocationInput"]
        )
        current_tool = tool_list[functionInvocationInput["function"]]
        parameters = dict()
        for param in functionInvocationInput["parameters"]:
            parameters[param["name"]] = param["value"]

        if current_tool.get_name() == "read_file":
            parameters = {"file_path": "document.txt"}

        output = json.dumps(current_tool.invoke(parameters))

        if "actionInvocationType" in functionInvocationInput:
            del functionInvocationInput["actionInvocationType"]
        if "collaboratorName" in functionInvocationInput:
            del functionInvocationInput["collaboratorName"]
        if "parameters" in functionInvocationInput:
            del functionInvocationInput["parameters"]

        functionInvocationInput["confirmationState"] = "CONFIRM"
        functionInvocationInput["responseState"] = "REPROMPT"
        functionInvocationInput["responseBody"] = dict()
        functionInvocationInput["responseBody"]["TEXT"] = dict()
        functionInvocationInput["responseBody"]["TEXT"]["body"] = output

        function_result = {"functionResult": functionInvocationInput}
        inlineSessionState["returnControlInvocationResults"].append(function_result)

    return inlineSessionState


def create_parameters(tool):
    parameters = dict()

    for key, value in tool.args.items():
        parameters[key] = dict()
        parameters[key]["description"] = value["description"]
        parameters[key]["type"] = value["type"]
        parameters[key]["required"] = True

    return parameters


def invoke_inline_agent_helper(request_params):

    print(request_params)
    response = bedrock_agents_runtime.invoke_inline_agent(**request_params)

    agent_answer = ""
    roc = None

    event_stream = response["completion"]

    try:
        for event in event_stream:

            # Get Final Answer
            if "chunk" in event:
                data = event["chunk"]["bytes"]
                agent_answer = data.decode("utf8")
                end_event_received = True

            # Process trace
            if "trace" in event and request_params["enableTrace"]:
                process_trace(event, "core")

            if "returnControl" in event:
                roc = event["returnControl"]
                return agent_answer, roc
        return agent_answer, roc

    except Exception as e:
        print(f"Caught exception while processing input to invokeAgent:\n")
        input_text = request_params["inputText"]
        print(f"  for input text:\n{input_text}\n")
        print(
            f"  request ID: {response['ResponseMetadata']['RequestId']}, retries: {response['ResponseMetadata']['RetryAttempts']}\n"
        )
        print(f"Error: {e}")
        raise Exception("Unexpected exception: ", e)


def process_trace(_event, trace_level):

    if "failureTrace" in _event["trace"]["trace"]:
        print(
            colored(
                f"Agent error: {_event['trace']['trace']['failureTrace']['failureReason']}",
                "red",
            )
        )

    if "orchestrationTrace" in _event["trace"]["trace"]:
        _orch = _event["trace"]["trace"]["orchestrationTrace"]

        if trace_level in ["core", "outline"]:
            if "rationale" in _orch:
                _rationale = _orch["rationale"]
                print(colored(f"{_rationale['text']}", "blue"))

            if "invocationInput" in _orch:
                # NOTE: when agent determines invocations should happen in parallel
                # the trace objects for invocation input still come back one at a time.
                _input = _orch["invocationInput"]
                print(_input)

                if "actionGroupInvocationInput" in _input:
                    if "function" in _input["actionGroupInvocationInput"]:
                        tool = _input["actionGroupInvocationInput"]["function"]
                    elif "apiPath" in _input["actionGroupInvocationInput"]:
                        tool = _input["actionGroupInvocationInput"]["apiPath"]
                    else:
                        tool = "undefined"
                    if trace_level == "outline":
                        print(
                            colored(
                                f"Using tool: {tool}",
                                "magenta",
                            )
                        )
                    else:
                        print(
                            colored(
                                f"Using tool: {tool} with these inputs:",
                                "magenta",
                            )
                        )
                        if (
                            len(_input["actionGroupInvocationInput"]["parameters"]) == 1
                        ) and (
                            _input["actionGroupInvocationInput"]["parameters"][0][
                                "name"
                            ]
                            == "input_text"
                        ):
                            print(
                                colored(
                                    f"{_input['actionGroupInvocationInput']['parameters'][0]['value']}",
                                    "magenta",
                                )
                            )
                        else:
                            print(
                                colored(
                                    f"{_input['actionGroupInvocationInput']['parameters']}\n",
                                    "magenta",
                                )
                            )

                elif "codeInterpreterInvocationInput" in _input:
                    if trace_level == "outline":
                        print(colored(f"Using code interpreter", "magenta"))
                    else:
                        console = Console()
                        _gen_code = _input["codeInterpreterInvocationInput"]["code"]
                        _code = f"```python\n{_gen_code}\n```"

                        console.print(Markdown(f"**Generated code**\n{_code}"))

            if "observation" in _orch:
                if trace_level == "core":
                    _output = _orch["observation"]
                    if "actionGroupInvocationOutput" in _output:
                        print(
                            colored(
                                f"--tool outputs:\n{_output['actionGroupInvocationOutput']['text']}...\n",
                                "magenta",
                            )
                        )

                    if "agentCollaboratorInvocationOutput" in _output:
                        _collab_name = _output["agentCollaboratorInvocationOutput"][
                            "agentCollaboratorName"
                        ]
                        _collab_output_text = _output[
                            "agentCollaboratorInvocationOutput"
                        ]["output"]["text"]
                        print(
                            colored(
                                f"\n----sub-agent {_collab_name} output text:\n{_collab_output_text}...\n",
                                "magenta",
                            )
                        )

                    if "finalResponse" in _output:
                        print(
                            colored(
                                f"Final response:\n{_output['finalResponse']['text']}...",
                                "cyan",
                            )
                        )
