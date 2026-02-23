// SOURCE EXAMPLE — from HAB-GPRSSubmission
// Used as a reference to test the transformer.
// See: Y:\Solution\HRSA\HAB-GPRSSubmission\src\GPRSSubmission.Web\wwwroot\gprs_app\ActionHistory\ActionHistory.component.ts

declare let module: any;
import { Component, OnInit, OnDestroy, Inject, AfterViewChecked } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { LayoutService } from 'pfm-layout/master';
import { EventPublisher } from 'pfm-ng/core';
import { Subject } from 'rxjs/Subject';

import { ActionHistoryPageActionModel } from './ActionHistory.PageActionModel';
import { QueryStringParams, GPRSConstants, ExternalSystemCode } from './../core/constants';
import { ContextService } from './../core/services/context.service';
import { ConfigurationService } from './../core/services/configuration.service';

@Component({
    selector: 'ActionHistory',
    templateUrl: 'ActionHistory.component.html',
    moduleId: module.id
})

export class ActionHistoryComponent implements OnInit, OnDestroy, AfterViewChecked {
    rtc = [];
    rlc = GPRSConstants.GPRSResourceTypeCode.toString();
    tid = ExternalSystemCode.GPRSWeb;
    strId: string;
    reportId: string;
    resourceValue: string[];

    pageTitle: string = 'Action History';
    pageActionModel: ActionHistoryPageActionModel;

    ngUnsubscribe: Subject<any>;
    constructor(
        @Inject(LayoutService) private layoutService: LayoutService,
        @Inject(ActivatedRoute) private route: ActivatedRoute,
        @Inject(ContextService) private contextService: ContextService,
        @Inject(EventPublisher) private eventPublisher: EventPublisher,
        @Inject(ConfigurationService) private configService: ConfigurationService
    ) {
        this.configService.showTopMenu = false;
        this.configService.showLeftMenu = false;

        this.ngUnsubscribe = new Subject();
        this.pageActionModel = new ActionHistoryPageActionModel();

        if (this.route.snapshot.queryParams[QueryStringParams.UserActionProcessTypeCode] !== null) {
            this.rtc.push(this.route.snapshot.queryParams[QueryStringParams.UserActionProcessTypeCode]);
        } else {
            this.rtc.push(GPRSConstants.GPRSUserActionProcessTypeWorkflow.toString());
        }
    }

    ngOnInit(): void {
        this.contextService.pageTitle = this.pageTitle;
        this.strId = this.route.snapshot.queryParams[QueryStringParams.StructuredTAReportId];
        this.resourceValue = [this.strId];

        this.pageActionModel.close.takeUntil(this.ngUnsubscribe).subscribe(() => { this.close() });
    }

    ngOnDestroy() {
        this.ngUnsubscribe.next();
        this.ngUnsubscribe.complete();
    }

    ngAfterViewChecked() {
        let divColRight = document.getElementById('colright');
        if (divColRight !== null) {
            divColRight.classList.remove("contentPaddingSideMenuVisible");
        }
    }

    close() {
        window.close();
    }
}
