import os
import json
from torch.autograd import Variable
from torch.utils.data import DataLoader
from six.moves import range

def dumpData(params,
               dataset,
               split,
               aBot,
               qBot,
               beamSize=5,
               saveFolder="dialog_output"):
    '''
        Generates dialog and saves it to a json for later visualization.
        Dialog is generated by both agents conversing (A-Bot is shown the GT image and both
        agents have access to a caption generated by a pre-trained captioning model).

        Arguments:
            params  : Parameter dict for all options
            dataset : VisDialDataset instance
            split   : Dataset split, can be 'val' or 'test'
            aBot    : A-Bot
            qBot    : Q-Bot

            beamSize : Beam search width for generating utterrances
            saveFolder : Folder path for saving dialog related files
    '''
    text = run_dialog(params,
               dataset,
               split,
               aBot,
               qBot,
               beamSize=beamSize)

    savePathJson = os.path.join(saveFolder,"results.json")

    with open(savePathJson, "w") as fp:
        print("Writing dialog text data to file: {}".format(savePathJson))
        json.dump(text, fp)

    print("Done!")

def run_dialog(params,
               dataset,
               split,
               aBot,
               qBot,
               beamSize=5):

    assert (qBot is not None and aBot is not None),\
                            "Must provide both Q-Bot and A-Bot when generating dialog"
    old_split = dataset.split
    batchSize = dataset.batchSize
    numRounds = dataset.numRounds

    ind2word = dataset.ind2word
    to_str_gt = lambda w: str(" ".join([ind2word[x] for x in filter(lambda x:\
                    x>0,w.data.cpu().numpy())])) #.encode('utf-8','ignore')
    to_str_pred = lambda w, l: str(" ".join([ind2word[x] for x in list( filter(
        lambda x:x>0,w.data.cpu().numpy()))][:l.data.cpu()[0]])) #.encode('utf-8','ignore')

    dataset.split = split

    dataloader = DataLoader(
        dataset,
        batch_size=batchSize,
        shuffle=False,
        num_workers=0,
        collate_fn=dataset.collate_fn)

    text = {'data': []}
    if '%s_img_fnames' % split not in dataset.data.keys():
        print("[Error] Need coco directory and info as input " \
               "to -cocoDir and -cocoInfo arguments for locating "\
               "coco image files.")
        print("Exiting dialogDump without saving files.")
        return None

    getImgFileName = lambda x: dataset.data['%s_img_fnames' % split][x]

    tot_idx = 0
    output_dialog = True
    img_pool = None
    # this file consists a mapping between a image and pool of images. The list of images is the set of images the 
    # human study is done on. The image pool is relevant for evaluation purposes. The first 6 images are the 6 closest
    # images to a given image in the test split of v1.0 visdial. Therefore, this consists of the image itself along with
    # 5 nearest neighbors based on fc7 similarity. The images after these 6 images are randomly selected images.
    with open('data/human_study/img_pool.json', "r") as fp:
        img_pool = json.load(fp)

    for idx, batch in enumerate(dataloader):
        print("current batch:",idx)
        tot_idx = tot_idx + 1
        imgIds = [getImgFileName(x) for x in batch['index']]
        dialog = [{'dialog': [], 'image_id': imgId, 'img_pool':img_pool[imgId]} for imgId in imgIds]

        if dataset.useGPU:
            batch = {key: v.cuda() if hasattr(v, 'cuda')\
                else v for key, v in batch.items()}

        image = Variable(batch['img_feat'], volatile=True)
        caption = Variable(batch['cap'], volatile=True)
        captionLens = Variable(batch['cap_len'], volatile=True)

        if aBot:
            aBot.eval(), aBot.reset()
            aBot.observe(
                -1, image=image, caption=caption, captionLens=captionLens)
        if qBot:
            qBot.eval(), qBot.reset()
            qBot.observe(-1, caption=caption, captionLens=captionLens)
        questions = []

        for j in range(batchSize):
            caption_str = to_str_gt(caption[j])[8:-6]
            dialog[j]['caption'] = caption_str

        for round in range(numRounds):

            questions, quesLens = qBot.forwardDecode(
                beamSize=beamSize, inference='greedy')
            qBot.observe(round, ques=questions, quesLens=quesLens)
            aBot.observe(round, ques=questions, quesLens=quesLens)
            answers, ansLens = aBot.forwardDecode(
                beamSize=beamSize, inference='greedy')
            aBot.observe(round, ans=answers, ansLens=ansLens)
            qBot.observe(round, ans=answers, ansLens=ansLens)
            qBot.encoder()

            for j in range(batchSize):
                question_str = to_str_pred(questions[j], quesLens[j])
                answer_str = to_str_pred(answers[j], ansLens[j])
                if output_dialog:
                    dialog[j]['dialog'].append({
                        "answer": answer_str[8:],
                        "question": question_str[8:] + " "
                        })  # "8:" for indexing out initial <START>

        if output_dialog:
            text['data'].extend(dialog)

    text['opts'] = {
        'qbot': params['qstartFrom'],
        'abot': params['startFrom'],
        'beamSize': beamSize,
        'decoder': params['decoder'],
        'encoder': params['encoder'],
    }

    dataset.split = old_split

    return text
