/*=============================================================================
    Name    : GameChat.c
    Purpose : This file contains all of the logic for the game chating system.

    Created 7/23/1998 by ddunlop
    Copyright Relic Entertainment, Inc.  All rights reserved.
=============================================================================*/

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef HAVE_LIBCURL
  #include <curl/curl.h>
#endif

#include "Chatting.h"
#include "CommandNetwork.h"
#include "CommandWrap.h"
#include "Debug.h"
#include "FontReg.h"
#include "GameChat.h"
#include "LinkedList.h"
#include "mainrgn.h"
#include "Memory.h"
#include "Region.h"
#include "SoundEvent.h"
#include "SoundEventDefs.h"
#include "StringSupport.h"
#include "UIControls.h"
#include "Universe.h"
#include "utility.h"

#ifdef _WIN32
    #include <windows.h>
#endif

#ifdef _MSC_VER
    #define strncasecmp strnicmp
#else
    #include <strings.h>
#endif


/*=============================================================================
    Defines:
=============================================================================*/

#ifdef _WIN32
#define GC_FIBFile          "FEMan\\Game_Chat.FIB"
#else
#define GC_FIBFile          "FEMan/Game_Chat.FIB"
#endif
#define GC_ChatHistoryMax   200
#define GC_MAXCHARACTERS    256

#define GC_ChatToAll        1
#define GC_ChatToAllies     2
#define GC_RUTransfer       3

/* Phase 14B AI chat defines */
#define GC_AI_PORT    3000
#define GC_AI_MAX_MSG 512

/*=============================================================================
    Data:
=============================================================================*/

textentryhandle chatentrybox=NULL;
regionhandle    chatdrawregion=NULL;
featom          chatdrawatom=
{
    "ChatTextDraw",  // name
    FAF_Function,    // flags
    FA_UserRegion,   // status
    0,               // type
    0,               // borderWidth
    0,               // tabstop
    150,             // borderColor
    4,               // contentColor
    300, 32,         // x,      loadedX
    0, 0,            // y,      loadedY
    0, 0,            // width,  loadedWidth
    0, 0,            // height, loadedHeight
    NULL,            // pData
    NULL,            // attribs
    0,               // hotKeyModifiers
    {0, 0, 0, 0, 0}, // hotKey[FE_NumberLanguages]
    {0, 0},          // pad2[2]
    {0, 0},          // drawstyle[2]
    NULL,            // region
    {0, 0},          // pad[2]
};

fescreen       *gcScreenHandle=NULL;
rectangle       textentrypos;

// boolean indicating if the game chatting system is running or not.
bool32            gcRunning=FALSE;

// booleans to indicate if the game chatting system is accepting text at the moment or not.
bool32            InChatMode=FALSE;
static bool32     gcAIMode=FALSE;   /* Phase 14B: single-player AI chat mode */
sdword          MessageToAllies;
bool32            ViewingBuffer=FALSE;

//bool32            reset=FALSE;

fonthandle      chathistoryfont=0;
char            chathistoryfontname[64]="default.hff";

LinkedList      chathistorylist;
Node           *curPosition=NULL;
chathistory     threadtransfer[40];
sdword          numnewchat=0;

// tweakable variables for color

color           gcGamePrivateChatColor=colRGB(255,0,0);
color           gcGameWhisperedColor=colRGB(0,255,0);
color           gcGameNormalChatColor=colRGB(200,200,200);

void           *chatmutex=NULL;

BabyCallBack   *ScrollDownAutoBaby=NULL;
real32          GC_SCROLL_TIME=2.5f;
sdword          maxlines=3;
uword           RUTransferToPlayer;

sdword          chatwidth;

/*=============================================================================
    function prototypes:
=============================================================================*/

void gcInGameChatEntry(char *name, featom *atom);
void gcChatTextDraw(featom *atom, regionhandle region);

fecallback      gcCallBack[]=
{
    {gcInGameChatEntry      ,   "InGameChatEntry"   },
    {NULL                   ,   NULL                }
};

fedrawcallback gcDrawCallback[] =
{
    {gcChatTextDraw         ,   "ChatTextDraw"      },
    {NULL                   ,   NULL                }
};

/*=============================================================================
    Function Logic:
=============================================================================*/

/*-----------------------------------------------------------------------------
    Name        : gcLockGameChat
    Description : locks the list of chat for exclusive use.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcLockGameChat(void)
{
    dbgAssertAlwaysDo(SDL_mutexP(chatmutex) != -1);
}

/*-----------------------------------------------------------------------------
    Name        : gcUnLockGameChat
    Description : Unlocks list of chat from exclusive use.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcUnLockGameChat(void)
{
    dbgAssertAlwaysDo(SDL_mutexV(chatmutex) != -1);
}

/*-----------------------------------------------------------------------------
    Name        : gcRemoveAmpersands
    Description : removes all '&' from the string
    Inputs      : dest, source
    Outputs     :
    Parameters  : void
    Return      : void
-----------------------------------------------------------------------------*/
void gcRemoveAmpersands(char *dest, char *source)
{
    sdword index, pos;

    for (index=0,pos=0; index<strlen(source); index++)
    {
        if (source[index] != '&') dest[pos++] = source[index];
    }
}

/*-----------------------------------------------------------------------------
    Name        : gcAddChatItemToList
    Description : this function adds a chat item to the chat history list
    Inputs      : chat item to add.
    Outputs     : none
    Return      : none
----------------------------------------------------------------------------*/
void gcAddChatItemToList(chathistory *chat)
{
    sdword width, nCharacters, addwidth, length;
    char   temp[256];
    chathistory *wrap;
    color  col = colBlack;

    switch (chat->messageType)
    {
        case GC_NORMALMESSAGE:
        {
            sprintf(temp,"<%s>  ",playerNames[chat->playerindex]);
            width = fontWidth(temp);
            col = gcGameNormalChatColor;
        }
        break;
        case GC_WHISPEREDMESSAGE:
        {
            sprintf(temp,"<%s>%s  ",playerNames[chat->playerindex], strGetString(strWhisperedMessage));
            width = fontWidth(temp);
            col = gcGamePrivateChatColor;
        }
        break;
        case GC_WRAPMESSAGE:
        {
            width = chat->indent;
            col = chat->col;
        }
        break;
        case GC_TEXTMESSAGE:
        {
            width = 0;
            col = chat->col;
        }
        break;
        default:
        {
            width = 0;
        }
        break;
    }

    if (width+fontWidth(chat->chatstring) > chatwidth)
    {
        wrap = (chathistory *)memAlloc(sizeof(chathistory),"InGameChat",NonVolatile);
        strcpy(wrap->userName, chat->userName);
        wrap->playerindex = chat->playerindex;
        wrap->messageType = GC_WRAPMESSAGE;
        wrap->col         = col;
        wrap->indent      = 0;

        nCharacters = strlen(chat->chatstring);
        addwidth = fontWidth(chat->chatstring);
        while (nCharacters>0 && width + addwidth > chatwidth)
        {
            addwidth = fontWidthN(chat->chatstring,nCharacters);
            nCharacters--;
        }

        length = nCharacters;

        while ((chat->chatstring[nCharacters] != ' ') && (nCharacters > 0) )
        {
            nCharacters--;
        }
        if (nCharacters == 0)
        {
            strcpy(wrap->chatstring, chat->chatstring + length);
            chat->chatstring[length] = 0;
        }
        else
        {
            strcpy(wrap->chatstring, chat->chatstring + nCharacters + 1);
            chat->chatstring[nCharacters] = 0;
        }

        listAddNodeBefore(chathistorylist.tail, &chat->link, chat);

        if (curPosition==chathistorylist.tail)
        {
            curPosition = curPosition->prev;
        }

        if (chathistorylist.num>GC_ChatHistoryMax)
        {
            if (curPosition == chathistorylist.head)
            {
                curPosition = curPosition->next;
            }
            listDeleteNode(chathistorylist.head);
        }

        gcAddChatItemToList(wrap);
    }
    else
    {
        listAddNodeBefore(chathistorylist.tail, &chat->link, chat);
    }

    if (curPosition==chathistorylist.tail)
    {
        curPosition = curPosition->prev;
    }

    if (chathistorylist.num>GC_ChatHistoryMax)
    {
        if (curPosition == chathistorylist.head)
        {
            curPosition = curPosition->next;
        }
        listDeleteNode(chathistorylist.head);
    }

//    reset = TRUE;
}



/*-----------------------------------------------------------------------------
    Name        : gcChatEntryStart
    Description : starts up the chat text entry system.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcChatEntryStart(bool32 toAllies)
{
    InChatMode = TRUE;
    if (toAllies)
    {
        MessageToAllies = GC_ChatToAllies;
        feScreenStart(ghMainRegion,"Allies_Chatting_Screen");
    }
    else
    {
        MessageToAllies = GC_ChatToAll;
        feScreenStart(ghMainRegion,"Say_Chatting_Screen");
    }
}

/*-----------------------------------------------------------------------------
    Name        : gcRUTransferStart
    Description : starts up the text entry box for an RU transfer.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcRUTransferStart(uword playertosendto)
{
    InChatMode = TRUE;
    MessageToAllies = GC_RUTransfer;
    RUTransferToPlayer = playertosendto;
    feScreenStart(ghMainRegion,"Allies_Chatting_Screen");
}

sdword gcParseChatEntry(char *message)
{
    udword      i,nummatches,index;
    sdword      userindex=-1;
    char        toname[64], ampremoved[64];
    bool32        done=FALSE;

    if (message[0]!='/') return(-1);

    message++;

    i=0;

    if (message[0] == 0) done = TRUE;

    while (!done)
    {
        toname[i] = message[0];
        i++;
        toname[i]=0;
        message++;

        if (userindex==-1)
        {
            nummatches = 0;

            for (index=0;index<universe.numPlayers;index++)
            {
                gcRemoveAmpersands(ampremoved, playerNames[index]);
                if (strncasecmp(ampremoved,toname,i)==0)
                {
                    userindex = index;

                    nummatches++;
                }
            }

            if (nummatches!=1)
            {
                userindex=-1;
            }
        }

        if (userindex != -1)
        {
            return (userindex);
        }

        if (message[0] == 0)
        {
            done = TRUE;
        }
    }

    return -1;
}

/*-----------------------------------------------------------------------------
    Name        : gcInGameChatEntry
    Description : Handles the chat text entry box messages.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcInGameChatEntry(char *name, featom *atom)
{
    sdword user = -1;   /* initialize to sentinel — fixes playerNames[-1] UB */
    chathistory *chat;
    char         temp[60];
    sdword       ruentered;

    if (MessageToAllies == GC_RUTransfer)
    {
        if (FEFIRSTCALL(atom))
        {
            chatentrybox = (textentryhandle)atom->pData;
            uicTextEntryInit(chatentrybox,UICTE_NoTextures|UICTE_ChatTextEntry|UICTE_NumberEntry);
            bitSet(chatentrybox->reg.status, RSF_KeyCapture);
            keyBufferClear();
            bitSet(chatentrybox->textflags, UICTE_DropShadow);
            chatentrybox->shadowColor = colBlack;
            uicTextBufferResize(chatentrybox,GC_MAXCHARACTERS);
            return;
        }
        switch (uicTextEntryMessage(atom))
        {
            case CM_AcceptText :
            {
                ruentered = atol(chatentrybox->textBuffer);
                //sendRUTransfer((udword)PLAYER_MASK(RUTransferToPlayer), ruentered);
                clWrapRUTransfer(&universe.mainCommandLayer,sigsPlayerIndex,RUTransferToPlayer, ruentered,0);
                //universe.players[sigsPlayerIndex].resourceUnits-=ruentered;
                InChatMode = FALSE;
                feScreenDisappear(NULL,NULL);
            }
            break;
            case CM_RejectText :
            {
                InChatMode = FALSE;
                feScreenDisappear(NULL,NULL);
            }
            break;
            case CM_KeyPressed:
            {
                 ruentered = atol(chatentrybox->textBuffer);
                 if (ruentered > universe.players[sigsPlayerIndex].resourceUnits
                     || ruentered <= 0)
                 {
                     //backspace if ru's entered are more than player has, or if they're less than or equal to 0
                     uicBackspaceCharacter(chatentrybox);
                 }
            }
            break;
        }
    }
    else
    {
        if (FEFIRSTCALL(atom))
        {
            chatentrybox = (textentryhandle)atom->pData;
            uicTextEntryInit(chatentrybox,UICTE_NoTextures|UICTE_ChatTextEntry);
            bitSet(chatentrybox->reg.status, RSF_KeyCapture);
            keyBufferClear();
            bitSet(chatentrybox->textflags, UICTE_DropShadow);
            chatentrybox->shadowColor = colBlack;
            uicTextBufferResize(chatentrybox,GC_MAXCHARACTERS);
            return;
        }
        switch (uicTextEntryMessage(atom))
        {
            case CM_AcceptText :
                if (!chatentrybox) { InChatMode = FALSE; gcAIMode = FALSE; feScreenDisappear(NULL,NULL); break; }
                /* ── AI single-player intercept (Phase 14B) ── */
                if (gcAIMode) {
                    char aidisplay[GC_AI_MAX_MSG + 10];
                    snprintf(aidisplay, sizeof(aidisplay), "YOU: %s", chatentrybox->textBuffer);
                    gcProcessGameTextMessage(aidisplay, colRGBA(170, 215, 255, 255));
#ifdef HAVE_LIBCURL
                    {
                        char *msgcopy = malloc(strlen(chatentrybox->textBuffer) + 1);
                        if (msgcopy) {
                            strcpy(msgcopy, chatentrybox->textBuffer);
                            SDL_Thread *t = SDL_CreateThread(gcAISendThread, "gcAI", msgcopy);
                            if (t) SDL_DetachThread(t); else free(msgcopy);
                        }
                    }
#else
                    gcProcessGameTextMessage("MOTHERSHIP: (libcurl not linked)",
                                             colRGBA(200, 100, 100, 255));
#endif
                    uicTextEntrySet(chatentrybox, "", 0);
                    InChatMode = FALSE;
                    gcAIMode   = FALSE;
                    feScreenDisappear(NULL, NULL);
                    break;
                }

                chat = (chathistory *)memAlloc(sizeof(chathistory),"InGameChat",NonVolatile);

                if (MessageToAllies==GC_ChatToAllies)
                {
                    sendChatMessage(universe.players[sigsPlayerIndex].Allies,chatentrybox->textBuffer,(uword)sigsPlayerIndex);

                    strcpy(chat->chatstring, chatentrybox->textBuffer);
                    chat->messageType = GC_WHISPEREDMESSAGE;
                }
                else
                {
                    if ((user=gcParseChatEntry(chatentrybox->textBuffer))!=-1)
                    {
                        if (user!=sigsPlayerIndex)
                        {
                            sendChatMessage(PLAYER_MASK(user),chatentrybox->textBuffer+strlen(playerNames[user])+2,(uword)sigsPlayerIndex);

                            strcpy(chat->chatstring, chatentrybox->textBuffer+strlen(playerNames[user])+2);
                            chat->messageType = GC_WHISPEREDMESSAGE;

                    #ifdef DEBUG_STOMP
                            regVerify(chatdrawregion);
                    #endif
                            bitSet(chatdrawregion->status,RSF_DrawThisFrame);
                        }
                    }
                    else
                    {
                        sendChatMessage(OTHER_PLAYERS_MASK,chatentrybox->textBuffer,(uword)sigsPlayerIndex);

                        strcpy(chat->chatstring, chatentrybox->textBuffer+strlen(playerNames[user]));
                        chat->messageType = GC_NORMALMESSAGE;

                #ifdef DEBUG_STOMP
                        regVerify(chatdrawregion);
                #endif
                        bitSet(chatdrawregion->status,RSF_DrawThisFrame);
                    }
                }

                strcpy(chat->userName, utyName);
                chat->playerindex = sigsPlayerIndex;

                gcAddChatItemToList(chat);

                uicTextEntrySet(chatentrybox,"",0);
                InChatMode = FALSE;
                feScreenDisappear(NULL,NULL);
            break;
            case CM_RejectText :
                InChatMode = FALSE;
                feScreenDisappear(NULL,NULL);
            break;
            case CM_KeyPressed:
            {
                if ((user=gcParseChatEntry(chatentrybox->textBuffer))!=-1)
                {
                    sprintf(temp, "/%s ", playerNames[user]);
                    if (strlen(chatentrybox->textBuffer) < strlen(temp))
                        uicTextEntrySet(chatentrybox, temp, strlen(temp));
                }
            }
            break;
        }
    }
}

/*-----------------------------------------------------------------------------
    Name        : gcScrollDownAuto
    Description : this function is called in a tunable time frame and scrolls the contents of the chatwindow automatically.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
bool32 gcScrollDownAuto(udword num, void *data, struct BabyCallBack *baby)
{
    if (curPosition != NULL)
    {
        if (curPosition->next!=NULL)
        {
            if (mrRenderMainScreen)
            {
                curPosition = curPosition->next;

                // keep calling me
                return(FALSE);
            }
        }
    }

    // don't call me anymore
    ScrollDownAutoBaby = NULL;
    return(TRUE);
}


/*-----------------------------------------------------------------------------
    Name        : gcChatTextDraw
    Description : draws the chat history window and prompts for text entry.
    Inputs      : standard draw callbacks.
    Outputs     : none
    Return      : void
----------------------------------------------------------------------------*/
void gcChatTextDraw(featom *atom, regionhandle region)
{
    fonthandle      oldfont;
    sdword          x,y=region->rect.y0,lines=0;
    char            temp[512], *string = NULL;
    Node           *walk = NULL;
    chathistory    *chat = NULL;

    if (!mrRenderMainScreen) return;
    if (chathistoryfont == 0) return;   /* font not yet registered — skip draw */

    oldfont = fontMakeCurrent(chathistoryfont);
    fontShadowSet(FS_SE, colBlack);

    if (InChatMode)
    {
        switch (MessageToAllies)
        {
            case GC_ChatToAllies:
                //sprintf(temp,"To Allies: ");
                string = strGetString(strToAllies);
            break;
            case GC_ChatToAll:
                //sprintf(temp,"Say: ");
                string = strGetString(strSay);
            break;
            case GC_RUTransfer:
                //sprintf(temp,"RU Amount: ");
                string = strGetString(strRUAmount);
            break;
        }

        x = region->rect.x0;
        fontPrint(x,y,colWhite,string);
        y+= fontHeight(" ");
        lines++;
    }

    if (curPosition != NULL)
    {
        walk = curPosition;
    }

    if (walk!=NULL)
    {
        do
        {
            x = region->rect.x0;

            chat = listGetStructOfNode(walk);

            switch (chat->messageType)
            {
                case GC_NORMALMESSAGE:
                {
                    sprintf(temp,"<%s>",playerNames[chat->playerindex]);
                    fontPrint(x,y,tpGameCreated.playerInfo[chat->playerindex].baseColor,temp);
                    x+=fontWidth(temp);

                    sprintf(temp,"  %s",chat->chatstring);
                    fontPrint(x,y,gcGameNormalChatColor,temp);
                }
                break;
                case GC_WHISPEREDMESSAGE:
                {
                    sprintf(temp,"<%s>",playerNames[chat->playerindex]);
                    fontPrint(x,y,tpGameCreated.playerInfo[chat->playerindex].baseColor,temp);
                    x+=fontWidth(temp);

                    sprintf(temp, "%s", strGetString(strWhisperedMessage));
                    fontPrint(x,y,gcGameWhisperedColor, temp);
                    x+=fontWidth(temp);

                    sprintf(temp,"  %s",chat->chatstring);
                    fontPrint(x,y,gcGamePrivateChatColor,temp);
                }
                break;
                case GC_TEXTMESSAGE:
                {
                    fontPrint(x,y,chat->col,chat->chatstring);
                }
                break;
                case GC_BUFFERSTART:
                {
                    if (ViewingBuffer)
                    {
                        //sprintf(temp,"^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^ ^");
                        fontPrint(x,y,gcGameNormalChatColor,strGetString(strStartOfBuffer));
                    }
                }
                break;
                case GC_BUFFEREND:
                {
                    if (ViewingBuffer)
                    {
                        //sprintf(temp,"v v v v v v v v v v v v v v v v v v v v v v v v");
                        fontPrint(x,y,gcGameNormalChatColor,strGetString(strEndOfBuffer));
                    }
                }
                break;
                case GC_WRAPMESSAGE:
                {
                    x+= chat->indent;
                    fontPrint(x,y,chat->col,chat->chatstring);
                }
                break;
            }

            y += fontHeight(" ");
            lines++;

            walk = walk->next;
        }
        while ((walk!=NULL) && (lines < maxlines));

        if ((ScrollDownAutoBaby==NULL) && (!ViewingBuffer) && (curPosition->next != NULL))
        {
            ScrollDownAutoBaby = taskCallBackRegister(gcScrollDownAuto, 0, NULL, GC_SCROLL_TIME);
        }
    }

    fontShadowSet(FS_NONE, colBlack);
    fontMakeCurrent(oldfont);
}

/*-----------------------------------------------------------------------------
    Name        : gcProcessGameChatPacket
    Description : this function processes the chat packet recieved from the game.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcProcessGameChatPacket(struct ChatPacket *packet)
{
    udword mask;

    gcLockGameChat();

    strcpy(threadtransfer[numnewchat].chatstring,packet->message);
    strcpy(threadtransfer[numnewchat].userName,  playerNames[packet->packetheader.frame]);
    threadtransfer[numnewchat].playerindex = packet->packetheader.frame;

    mask = PLAYER_MASK(sigsPlayerIndex);
    mask &= packet->users;

    if (mask==packet->users)
        threadtransfer[numnewchat].messageType = GC_WHISPEREDMESSAGE;
    else
        threadtransfer[numnewchat].messageType = GC_NORMALMESSAGE;

    numnewchat++;

        soundEvent(NULL, UI_ChatMessage);

    gcUnLockGameChat();
}


/*-----------------------------------------------------------------------------
    Name        : gcProcessGameTextMessage
    Description : this function processes the chat packet recieved from the game.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcProcessGameTextMessage(char *message, udword col)
{
    gcLockGameChat();

    strcpy(threadtransfer[numnewchat].chatstring,message);

    threadtransfer[numnewchat].messageType = GC_TEXTMESSAGE;
    threadtransfer[numnewchat].col         = col;

    numnewchat++;

    gcUnLockGameChat();
}

/*-----------------------------------------------------------------------------
    Name        : gcPageUpProcess
    Description : processes the page up key when it is pressed.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcPageUpProcess(void)
{
    sdword i,amount=maxlines;

    if (!multiPlayerGame) return;

    ViewingBuffer = TRUE;

    if (ScrollDownAutoBaby!=NULL)
    {
        taskCallBackRemove(ScrollDownAutoBaby);
        ScrollDownAutoBaby = NULL;
    }

    for (i=0;i<amount;i++)
    {
        if (curPosition->prev != NULL)
        {
            curPosition = curPosition->prev;
        }
    }
}

/*-----------------------------------------------------------------------------
    Name        : gcPageDownProcess
    Description : processes the page down key when it is pressed.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcPageDownProcess(void)
{
    sdword i, amount=maxlines;

    if (!multiPlayerGame) return;

    ViewingBuffer = TRUE;

    if (ScrollDownAutoBaby!=NULL)
    {
        taskCallBackRemove(ScrollDownAutoBaby);
        ScrollDownAutoBaby = NULL;
    }

    for (i=0;i<amount;i++)
    {
        if (curPosition->next != NULL)
        {
            curPosition = curPosition->next;
        }
        else
        {
            ViewingBuffer = FALSE;
        }
    }
}

/*-----------------------------------------------------------------------------
    Name        : gcDoneViewingBuffer
    Description :
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcCancelViewingBuffer(void)
{
    if (chathistorylist.tail != NULL)
    {
        curPosition = chathistorylist.tail;
    }
    ViewingBuffer = FALSE;
}



/*-----------------------------------------------------------------------------
    Name        : gcPollForNewChat
    Description : This function is called every univ update to
                  check if new chat messages have arrived or not.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcPollForNewChat(void)
{
    chathistory *chat;
    sdword i;

    gcLockGameChat();

    if (numnewchat)
    {
        for (i=0;i<numnewchat;i++)
        {
            chat = (chathistory *)memAlloc(sizeof(chathistory),"InGameChat",NonVolatile);

            strcpy(chat->chatstring, threadtransfer[i].chatstring);
            strcpy(chat->userName, threadtransfer[i].userName);

            chat->messageType = threadtransfer[i].messageType;
            chat->col         = threadtransfer[i].col;

            chat->playerindex = threadtransfer[i].playerindex;

            gcAddChatItemToList(chat);
        }

#ifdef DEBUG_STOMP
        regVerify(chatdrawregion);
#endif
        bitSet(chatdrawregion->status,RSF_DrawThisFrame);

        numnewchat = 0;
    }

    gcUnLockGameChat();
}

/*-----------------------------------------------------------------------------
    Name        : gcAITalkBegin / gcAISendThread — single-player AI chat (Phase 14B)
=============================================================================*/

#ifdef HAVE_LIBCURL

static size_t gcAICurlWrite(void *ptr, size_t sz, size_t nm, void *ud)
{
    char *buf = (char *)ud;
    size_t bytes = sz * nm;
    size_t existing = strlen(buf);
    if (existing + bytes < GC_AI_MAX_MSG - 1) {
        memcpy(buf + existing, ptr, bytes);
        buf[existing + bytes] = '\0';
    }
    return bytes;
}

static const char *gcAIJsonStr(const char *json, const char *key,
                                char *out, size_t outsz)
{
    char search[64];
    snprintf(search, sizeof(search), "\"%s\":\"", key);
    const char *p = strstr(json, search);
    if (!p) return NULL;
    p += strlen(search);
    size_t i = 0;
    while (*p && *p != '"' && i < outsz - 1) {
        if (*p == '\\' && *(p+1)) { p++; out[i++] = (*p=='n') ? '\n' : *p; }
        else out[i++] = *p;
        p++;
    }
    out[i] = '\0';
    return out;
}

static int gcAISendThread(void *arg)
{
    char *msg = (char *)arg;    /* heap-allocated message string */
    char url[64];
    char body[GC_AI_MAX_MSG + 128];
    char respbuf[GC_AI_MAX_MSG];

    /* Quote escaping */
    char safe[GC_AI_MAX_MSG * 2];
    int j = 0;
    for (int i = 0; msg[i] && j < (int)sizeof(safe)-2; i++) {
        if (msg[i] == '"') safe[j++] = '\\';
        safe[j++] = msg[i];
    }
    safe[j] = '\0';
    free(msg);

    snprintf(url, sizeof(url), "http://127.0.0.1:%d/api/console", GC_AI_PORT);
    snprintf(body, sizeof(body),
             "{\"ship_id\":\"MOTHERSHIP\",\"ship_type\":\"mothership\",\"message\":\"%s\"}",
             safe);

    respbuf[0] = '\0';
    CURL *curl = curl_easy_init();
    if (curl) {
        struct curl_slist *hdrs = curl_slist_append(NULL,
                                      "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_URL,           url);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS,    body);
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER,    hdrs);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, gcAICurlWrite);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA,     respbuf);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT,       30L);
        CURLcode rc = curl_easy_perform(curl);
        if (rc == CURLE_OK) {
            char reply[GC_AI_MAX_MSG] = "";
            if (gcAIJsonStr(respbuf, "reply", reply, sizeof(reply)) && reply[0]) {
                char display[GC_AI_MAX_MSG + 20];
                snprintf(display, sizeof(display), "MOTHERSHIP: %s", reply);
                gcProcessGameTextMessage(display, colRGBA(90, 240, 130, 255));
            }
        } else {
            gcProcessGameTextMessage("MOTHERSHIP: (server unreachable)",
                                     colRGBA(255, 100, 100, 255));
        }
        curl_slist_free_all(hdrs);
        curl_easy_cleanup(curl);
    }
    return 0;
}

#endif /* HAVE_LIBCURL */

/* Lightweight chat init safe to call mid-game (skips regChildAlloc). */
static void gcAIStartup(void)
{
    chathistory *dummy;

    chathistoryfont = frFontRegister(chathistoryfontname);

    listInit(&chathistorylist);
    dummy = memAlloc(sizeof(chathistory), "DummyChatThing", NonVolatile);
    dummy->messageType = GC_BUFFERSTART;
    listAddNode(&chathistorylist, &dummy->link, dummy);
    dummy = memAlloc(sizeof(chathistory), "DummyChatThing", NonVolatile);
    dummy->messageType = GC_BUFFEREND;
    listAddNode(&chathistorylist, &dummy->link, dummy);
    curPosition = &dummy->link;

    chatmutex = SDL_CreateMutex();

    /* Load FIB + register callbacks (safe to call mid-game) */
    feScreensLoad(GC_FIBFile);
    feCallbackAddMultiple(gcCallBack);
    feDrawCallbackAddMultiple(gcDrawCallback);
    gcScreenHandle = feScreenFind("Say_Chatting_Screen");
    /* feScreenFind is fatal if not found — if we get here the screen is valid */

    /* Intentionally skip regChildAlloc — chatdrawregion stays NULL.
       The text input works via feScreenStart without a persistent draw region. */

    gcRunning = TRUE;
}

/* Open the AI Talk interface (Y key — any game mode). */
void gcAITalkBegin(void)
{
    if (InChatMode) return;                    /* already open */
    if (!gcRunning) gcAIStartup();             /* safe mid-game init */
    if (!gcRunning) return;                    /* FIB not found */
    if (!chathistoryfont) return;              /* font not ready */
    gcAIMode = TRUE;
    gcChatEntryStart(FALSE);
}

/*-----------------------------------------------------------------------------
    Name        : gcStartup
    Description : This initializes the game chatting system, adding its regions to the game etc.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcStartup(void)
{
    chathistory   *dummy;

    chathistoryfont = frFontRegister(chathistoryfontname);

    listInit(&chathistorylist);
    dummy = memAlloc(sizeof(chathistory), "DummyChatThing", NonVolatile);
    dummy->messageType = GC_BUFFERSTART;
    listAddNode(&chathistorylist,&dummy->link,dummy);
    dummy = memAlloc(sizeof(chathistory), "DummyChatThing", NonVolatile);
    dummy->messageType = GC_BUFFEREND;
    listAddNode(&chathistorylist,&dummy->link,dummy);
    curPosition = &dummy->link;

    chatmutex = SDL_CreateMutex();

    if (chatdrawregion!=NULL)
    {
        regChildInsert(chatdrawregion, ghMainRegion);
    }
    else
    {
        feScreensLoad(GC_FIBFile);
        feCallbackAddMultiple(gcCallBack);
        feDrawCallbackAddMultiple(gcDrawCallback);
        gcScreenHandle = feScreenFind("Say_Chatting_Screen");

        if (!gcScreenHandle) return;   /* FIB not found — abort silently */

        chatdrawatom.x      = gcScreenHandle->atoms[0].x;
        chatdrawatom.y      = gcScreenHandle->atoms[0].y;
        chatdrawatom.width  = gcScreenHandle->atoms[0].width;
        chatdrawatom.height = gcScreenHandle->atoms[0].height;
        chatdrawatom.pData = (unsigned char *)gcChatTextDraw;

        chatwidth = chatdrawatom.width;

        textentrypos.x0     = gcScreenHandle->atoms[1].x;
        textentrypos.y0     = gcScreenHandle->atoms[1].y;
        textentrypos.x1     = gcScreenHandle->atoms[1].x+gcScreenHandle->atoms[1].width;
        textentrypos.y1     = gcScreenHandle->atoms[1].y+gcScreenHandle->atoms[1].height;

        chatdrawregion = regChildAlloc(ghMainRegion, (sdword)&chatdrawatom, chatdrawatom.x, chatdrawatom.y,
                                       chatdrawatom.width, chatdrawatom.height, 0, 0);
        chatdrawatom.region = (void*)chatdrawregion;
        regDrawFunctionSet(chatdrawregion, feUserRegionDraw);

        chatdrawregion->drawstyle[0] = chatdrawatom.drawstyle[0];
        chatdrawregion->drawstyle[1] = chatdrawatom.drawstyle[1];
        chatdrawregion->atom = &chatdrawatom;
    }

    gcRunning=TRUE;
}

/*-----------------------------------------------------------------------------
    Name        : gcShutdown
    Description : This shutsdown the game chatting system.
    Inputs      :
    Outputs     :
    Return      : void
----------------------------------------------------------------------------*/
void gcShutdown(void)
{
    SDL_DestroyMutex(chatmutex);
    chatmutex = NULL;

    curPosition = NULL;

    if (chatdrawregion!=NULL)
    {
        regLinkRemove(chatdrawregion);
    }
    listDeleteAll(&chathistorylist);

    gcRunning=FALSE;
}


