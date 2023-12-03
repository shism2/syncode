from mxeval.data import write_jsonl, read_problems, get_data
from transformers import (
    PreTrainedModel,
    PreTrainedTokenizer,
)
from tqdm import tqdm
import typing

BatchGenerator = typing.Callable[
    [PreTrainedModel, PreTrainedTokenizer, str, int], list[str]
]

# reference: https://github.com/declare-lab/instruct-eval/blob/main/human_eval/main.py#L35
def filter_code(completion: str) -> str:
    # The program tends to overwrite, we only take the first function 
    completion = completion.lstrip("\n")
    return completion.split("\n\n")[0]


def fix_indents(text: str) -> str:
    return text.replace("\t", "    ")


def split_batch(samples: list[str], size=4):
    mini_batches = []

    for i in range(0, len(samples), size):
        mini_batches.append(samples[i : i + size])

    return mini_batches


def run_eval(args, 
    hf_model,
    num_samples_per_task: int,
    out_path: str,
    format_tabs: bool = False,
):
    problems = get_data(args.dataset, args.language)
    samples = []
    pbar = tqdm(total=len(problems) * num_samples_per_task)

    for task_id in problems:
        if format_tabs:
            prompt = problems[task_id]["prompt"].replace("    ", "\t")
        else:
            prompt = problems[task_id]["prompt"]

        batch_completions = hf_model.generate_batch_completion(prompt, num_samples_per_task)

        for raw_sample in batch_completions:
            # Post-processing to filter out single function code for each language
            if args.language == "python": 
                sample = filter_code(fix_indents(raw_sample))
            elif args.language == "go":
                sample = raw_sample + '}\n'
                sample = filter_code(sample)
            
            print('Raw sample:\n', raw_sample)   
            print('Filtered sample:\n', sample)

            result = dict(
                task_id=task_id,
                language=problems[task_id]["language"],
                completion=sample
            )
            samples += [result]
        
        pbar.update(num_samples_per_task)
    write_jsonl(out_path, samples)
